import os
import json
import traceback

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'emog_project.settings')
import django
django.setup()

from django.conf import settings
from openai import OpenAI

omni_cfg = getattr(settings, 'OMNIROUTE', {})

def try_base(base_url):
    print('Trying base_url=', base_url)
    client = OpenAI(base_url=base_url, api_key=omni_cfg.get('API_KEY', 'omniroute-local'), timeout=omni_cfg.get('TIMEOUT', 30))
    try:
        resp = client.chat.completions.create(
            model=getattr(settings, 'OMNIROUTE_DEFAULT_MODEL', 'auto/best-chat'),
            messages=[{'role':'system','content':'You are a test.'},{'role':'user','content':'Hello'}],
            temperature=0,
        )
        print('SUCCESS for', base_url)
        print(json.dumps(resp, default=str)[:2000])
    except Exception:
        print('EXCEPTION for', base_url)
        traceback.print_exc()


def main():
    base = omni_cfg.get('BASE_URL', 'http://localhost:3001/v1')
    # try original
    try_base(base)
    # try without trailing /v1 if present
    if base.rstrip('/').endswith('/v1'):
        alt = base.rstrip('/')[: -3]
        try_base(alt)

if __name__ == '__main__':
    main()
