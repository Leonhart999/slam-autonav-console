"""Backend for the eye_care_ui plugin: persists user eye-care tuning defaults via plugin settings."""


def setup(ctx):
    @ctx.router.get('/config')
    async def get_config():
        s = ctx.settings()
        return {
            'default_filter_strength': int(s.get('default_filter_strength', 40)),
            'default_warmth': int(s.get('default_warmth', 3)),
            'auto_apply_on_load': bool(s.get('auto_apply_on_load', False)),
        }
