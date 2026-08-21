import requests
try:
    r = requests.get('http://127.0.0.1:8793/v1/mobile/live/state/v3/pocket-live-8788?mode=CLEAN_LIVE&compact=false', timeout=10)
    d = r.json()
    cs = d.get('capture_source_v3', {})
    print('Capture state:', cs.get('state'))
    print('Decision usable:', cs.get('decision_usable'))
    print('Frame ID:', d.get('frame_id'))
    print('Capture count:', d.get('capture_count'))
    print('Overlays count:', d.get('overlays',{}).get('count'))
    print('Signal:', d.get('latest_signal',{}).get('action'))
except Exception as e:
    print('Error:', e)