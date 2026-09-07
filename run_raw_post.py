import urllib.request, json, traceback

url='http://127.0.0.1:3001/v1/chat/completions'
body=json.dumps({'model':'auto/best-chat','messages':[{'role':'system','content':'You are a test.'},{'role':'user','content':'Hello'}]}).encode()
req=urllib.request.Request(url,data=body,headers={'Content-Type':'application/json','Authorization':'Bearer omniroute-local'})
try:
    with urllib.request.urlopen(req,timeout=10) as resp:
        print('STATUS',resp.status)
        data=resp.read(2000)
        print(data.decode(errors='replace'))
except Exception:
    traceback.print_exc()
