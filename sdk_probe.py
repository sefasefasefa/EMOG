import json
import traceback
from openai import OpenAI

def probe(base_url):
    print('Probing', base_url)
    client = OpenAI(base_url=base_url, api_key='omniroute-local', timeout=30)
    try:
        resp = client.chat.completions.create(
            model='auto/best-chat',
            messages=[{'role':'system','content':'You are a test.'},{'role':'user','content':'Hello'}],
            temperature=0,
        )
        print('SUCCESS')
        print(json.dumps(resp, default=str)[:2000])
    except Exception as e:
        print('EXCEPTION:')
        traceback.print_exc()

if __name__ == '__main__':
    # try both with and without /v1
    probe('http://127.0.0.1:3002/v1')
    probe('http://127.0.0.1:3002')
