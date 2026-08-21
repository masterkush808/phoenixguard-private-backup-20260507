import uvicorn
from phoenixguard.mobile_api.app import create_app

app = create_app()
uvicorn.run(app, host='127.0.0.1', port=8793, log_level='info')