from pyngrok import ngrok
import time
public_url = ngrok.connect(8000).public_url
print("NGROK_URL:" + public_url)
with open("ngrok_url.txt", "w") as f:
    f.write(public_url)
while True:
    time.sleep(1)
