"""The question-answer loop: the model reads console data (and, when the user allows it, runs experiments) with
tools until it can answer.

Runs in a worker thread (the providers are blocking HTTP clients). emit() sends progress events to the browser;
call_tool() runs a tool on the server's event loop; confirm() asks the user to approve a run tool and waits;
cancelled() turns true when the browser stops waiting.

Access levels (chosen per question in the chat panel):
  chat  no tools (tool_choice none), the model answers from the conversation
  read  read tools; run tools are refused
  ask   read tools; every run tool call waits for the user's approval
  auto  read and run tools without asking
The full tool list is always sent (a stable prompt prefix, and old tool calls in the history stay valid); the access
level is told to the model with the question and enforced here.
"""
from . import actions
from .providers import ProviderError

SYSTEM = """You are the assistant built into the SLAM and AutoNav Console, a web console for LiDAR SLAM mapping \
(FAST-LIO2 with optional LiDAR-only and ground-vehicle-constraint variants, GLIM), rosbag playback of real-robot data, \
map management, Nav2 navigation and evaluation with evo. You help the user understand their maps, runs, \
batch experiments, SLAM parameters, logs and sensor profiles.

There are two kinds of tools. Read tools only read data. Run tools (their description starts with RUN TOOL) start or \
cancel batch experiments and compute evaluations. Each question carries the tool access the user chose in the chat \
panel: "chat" (no tools), "read" (run tools are refused), "ask" (the user approves every run tool call) or "auto". \
Follow it: when the user wants an experiment but run tools are not allowed, say what you would start and that they \
can switch the access menu below the input to "Ask before running", or where in the console it is done (pages: \
Mapping, Playback, Maps, Navigation, Evaluation, Monitor, Plugins). You cannot delete anything or change settings.

Running experiments:
- Call get_experiment_options first and use only the presets, SLAM stacks, variants, maps and speeds it lists.
- Start exactly what the user asked for; when the request is vague (no scene, no repetition count), ask first.
- A batch takes minutes per run and runs in the background. After starting it, say what was started and where to \
follow it (Evaluation page); do not poll it in a loop. Its results can be read later with get_batch.
- If the user declines a run tool call, do not try again in another way; ask what they want instead.

How to read the data:
- ATE (ape_rmse) is evo APE translation RMSE after SE(3) alignment against ground truth; RPE (rpe_rmse) uses 1 m \
segments. Only runs with ground truth have them: simulation runs and bags that contain ground truth.
- Playback of real-robot bags usually has no ground truth. ref_ape_rmse compares with the robot's wheel odometry, \
which drifts itself: it is a deviation, not an accuracy. start_end is the distance between the first and last pose \
(the loop closure error when the route ends where it started); z_end and z_max_abs are the height drift of a ground \
robot on a flat floor.
- A run's slam_config lists the parameters it actually used and where each value came from (base file, derived from \
sensor facts, or changed by the user). Compare runs with compare_runs to see parameter differences.
- Batch experiments repeat the same scene and SLAM N times; compare means together with standard deviations and N \
before calling one better.

Answer in the user's language. Be concise and specific: quote numbers with units, name the runs, maps or batches you \
used, and say plainly when the data cannot answer the question. Never invent values the tools did not return. \
Use Markdown (short lists or a table) when it helps."""

LIMIT_NOTE = 'The tool budget for this question is used up. Answer now with the data you already have.'


class Cancelled(Exception):
    pass


ACCESS = ('chat', 'read', 'ask', 'auto')
ACCESS_NOTE = {
    'chat': 'chat only: no tools, answer from the conversation',
    'read': 'read only: read tools are allowed, run tools will be refused',
    'ask': 'read tools allowed; every run tool call is shown to the user to approve first',
    'auto': 'read and run tools are allowed without asking',
}
CONTEXT_PREFIXES = ('[Console page currently open:', '[Tool access for this question:')


def context_lines(page, access):
    lines = [f'[Console page currently open: {page}]'] if page else []
    return lines + [f'[Tool access for this question: {access} ({ACCESS_NOTE[access]})]']


def strip_context(text):
    """The user's own words of a stored user turn (without the page / access lines added above)."""
    lines = text.split('\n')
    while lines and lines[0].startswith(CONTEXT_PREFIXES):
        lines.pop(0)
    return '\n'.join(lines)


def refusal(name, access):
    if access == 'chat':
        return ('Error: tools are switched off for this question (access "chat"). Answer without tools, or tell the '
                'user to choose "Read only" or higher in the access menu.')
    return (f'Error: {name} is a run tool and the user allows read tools only (access "read"). Do not retry; tell the '
            'user what you would run and that they can switch the access menu to "Ask before running".')


def run_turn(provider, session, user_text, page, settings, call_tool, emit, cancelled, access='read', confirm=None):
    turns = session['turns']
    if not session.get('title'):
        session['title'] = ' '.join(user_text.split())[:60]
    turns.append({'role': 'user', 'text': '\n'.join(context_lines(page, access) + [user_text])})
    system = SYSTEM
    if (settings.get('instructions') or '').strip():
        system += '\n\nAdditional instructions from the user:\n' + settings['instructions'].strip()
    defs = actions.definitions()
    choice = 'none' if access == 'chat' else 'auto'
    max_rounds = int(settings.get('max_tool_rounds') or 12)
    rounds, over_limit = 0, False
    usage = {'input': 0, 'output': 0}
    model = ''
    while True:
        if cancelled():
            raise Cancelled()
        emit({'type': 'status', 'state': 'model', 'round': rounds + 1})
        reply = provider.complete(system, turns, defs, choice)
        model = reply.model or model
        for k in usage:
            usage[k] += reply.usage.get(k) or 0
        turns.append({'role': 'assistant', 'text': reply.text, 'tool_calls': reply.tool_calls,
                      'raw': reply.raw, 'raw_provider': provider.name})
        if reply.stop == 'refusal':
            raise ProviderError('refusal', category=reply.detail or '-')
        if not reply.tool_calls:
            if reply.stop == 'pause':
                continue
            emit({'type': 'text', 'text': reply.text, 'final': True, 'truncated': reply.stop == 'max_tokens'})
            emit({'type': 'done', 'usage': usage, 'model': model, 'rounds': rounds})
            return
        if reply.text:
            emit({'type': 'text', 'text': reply.text, 'final': False})
        results = []
        try:
            for c in reply.tool_calls:
                if cancelled():
                    raise Cancelled()
                kind = actions.kind(c['name'])
                emit({'type': 'tool', 'id': c['id'], 'name': c['name'], 'input': c['input'], 'kind': kind or 'read'})
                decision = None
                if rounds >= max_rounds:
                    content, is_error = 'Error: ' + LIMIT_NOTE, True
                elif access == 'chat' or (kind == 'run' and access == 'read'):
                    content, is_error, decision = refusal(c['name'], access), True, 'blocked'
                else:
                    if kind == 'run':
                        decision = 'auto'
                        if access == 'ask':
                            emit({'type': 'status', 'state': 'confirm', 'round': rounds + 1})
                            decision = confirm(c) if confirm else 'denied'
                    if decision in ('denied', 'cancelled'):
                        content, is_error = ('Error: the user declined this call. Do not retry it; ask the user what '
                                             'they want instead.' if decision == 'denied'
                                             else 'Error: cancelled by the user'), True
                    else:
                        content, is_error = call_tool(c['name'], c['input'])
                result = {'id': c['id'], 'name': c['name'], 'content': content, 'is_error': is_error}
                if decision:
                    result['decision'] = decision
                results.append(result)
                emit({'type': 'tool_result', 'id': c['id'], 'ok': not is_error, 'chars': len(content),
                      'error': content[:300] if is_error else None, 'decision': decision})
                if decision == 'cancelled':
                    raise Cancelled()
        finally:
            # every tool call gets a result, also when the user stopped in the middle (the history stays valid)
            done = {r['id'] for r in results}
            results += [{'id': c['id'], 'name': c['name'], 'content': 'Error: cancelled by the user', 'is_error': True}
                        for c in reply.tool_calls if c['id'] not in done]
            turns.append({'role': 'tool', 'results': results})
        rounds += 1
        if rounds >= max_rounds and not over_limit:
            over_limit = True
            turns.append({'role': 'user', 'text': LIMIT_NOTE})
        elif over_limit and rounds >= max_rounds + 2:
            raise ProviderError('too_many_rounds', rounds=rounds)
