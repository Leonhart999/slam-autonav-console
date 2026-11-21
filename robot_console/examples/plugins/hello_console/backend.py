"""Backend of the example plugin: GET /api/ext/hello_console/summary."""


def setup(ctx):
    @ctx.router.get('/summary')
    async def summary():
        s = ctx.settings()
        if not s['greeting'].strip():
            raise ctx.error(409, 'empty_greeting')
        text = ' '.join([s['greeting']] * s['repeat']) + ('!' if s['tone'] == 'excited' else '')
        # Console data is read through the console's own REST API, in process
        stacks = await ctx.api.get('/api/stacks')
        maps = await ctx.api.get('/api/maps')
        return {
            'text': text,
            'running': [st['name'] for st in stacks if st.get('state') == 'running'],
            'stacks': len(stacks),
            'maps': len(maps),
            'token_set': bool(s['token']),
        }

    ctx.log.info('ready')
