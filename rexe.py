import os
import time
import requests
from datetime import datetime, timezone
import telebot

TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
OPENROUTER_API_KEY = "YOUR_OPENROUTER_API_KEY"

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

LOCAL_STORAGE_DIR = "yerel_dosyalar"
if not os.path.exists(LOCAL_STORAGE_DIR):
    os.makedirs(LOCAL_STORAGE_DIR)

FIREBASE_CONFIG = {
    "apiKey": "YOUR_FIREBASE_API_KEY",
    "projectId": "YOUR_FIREBASE_PROJECT_ID"
}

class LightFirestore:
    def __init__(self, config):
        self.project_id = config["projectId"]
        self.api_key = config["apiKey"]
        self.base_url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents"

    def _format_value(self, value):
        if isinstance(value, str): return {"stringValue": value}
        elif isinstance(value, bool): return {"booleanValue": value}
        elif isinstance(value, int): return {"integerValue": str(value)}
        elif isinstance(value, float): return {"doubleValue": float(value)}
        elif isinstance(value, datetime): return {"timestampValue": value.isoformat().replace("+00:00", "Z")}
        elif value is None: return {"nullValue": None}
        return {"stringValue": str(value)}

    def _parse_value(self, field_data):
        if "stringValue" in field_data: return field_data["stringValue"]
        elif "integerValue" in field_data: return int(field_data["integerValue"])
        elif "booleanValue" in field_data: return field_data["booleanValue"]
        elif "doubleValue" in field_data: return float(field_data["doubleValue"])
        return str(field_data)

    def _format_document(self, data):
        return {"fields": {k: self._format_value(v) for k, v in data.items()}}

    def _parse_document(self, doc_json):
        doc_id = doc_json.get("name", "").split("/")[-1]
        fields = doc_json.get("fields", {})
        data = {k: self._parse_value(v) for k, v in fields.items()}
        return doc_id, data

    def get_waiting_requests(self):
        url = f"{self.base_url}/istekler?key={self.api_key}"
        try:
            resp = requests.get(url)
            if resp.status_code == 200:
                docs = resp.json().get("documents", [])
                waiting_reqs = []
                for doc in docs:
                    doc_id, data = self._parse_document(doc)
                    if data.get("status") == "waiting":
                        waiting_reqs.append((doc_id, data))
                return waiting_reqs
            return []
        except:
            return []

    def create_document_with_id(self, collection, doc_id, data):
        url = f"{self.base_url}/{collection}/{doc_id}?key={self.api_key}"
        requests.patch(url, json=self._format_document(data))

    def delete_document(self, collection, doc_id):
        url = f"{self.base_url}/{collection}/{doc_id}?key={self.api_key}"
        requests.delete(url)

db = LightFirestore(FIREBASE_CONFIG)

def analyze_task(prompt):
    prompt_lower = prompt.lower()
    if any(k in prompt_lower for k in ["kod", "python", "yazılım", "hata"]):
        return {"type": "coding", "model": "deepseek/deepseek-v4-pro"}
    elif any(k in prompt_lower for k in ["analiz", "düşün", "mantık"]):
        return {"type": "reasoning", "model": "qwen/qwen3.6-27b"}
    elif any(k in prompt_lower for k in ["video", "görsel", "çiz", "üret"]):
        return {"type": "media", "model": "google/veo-3.1-lite"}
    else:
        return {"type": "assistant", "model": "qwen/qwen3.5-plus-20260420"}

def ask_openrouter(sys_prompt, user_prompt, model):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": model, 
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt}
        ]
    }
    
    response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=40)
    response.raise_for_status()
    
    result = response.json()
    if "choices" in result and len(result["choices"]) > 0:
        return result["choices"][0]["message"]["content"]
    raise Exception("API Hatası")

def handle_media_request(ai_response, chat_id, doc_id):
    file_name = f"rexe_video_{doc_id}.txt"
    file_path = os.path.join(LOCAL_STORAGE_DIR, file_name)
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(ai_response)
    
    try:
        if chat_id:
            with open(file_path, "rb") as doc_to_send:
                bot.send_document(chat_id, doc_to_send)
    except:
        pass

def start_worker():
    while True:
        try:
            waiting_requests = db.get_waiting_requests()
            
            for doc_id, request_data in waiting_requests:
                chat_id = request_data.get("chat_id", "")
                user_prompt = request_data.get("query", "")
                
                request_data["status"] = "processing"
                request_data["processing_time"] = datetime.now(timezone.utc)
                db.delete_document("istekler", doc_id)
                db.create_document_with_id("islenmis_istekler", doc_id, request_data)
                
                task = analyze_task(user_prompt)
                model = task["model"]
                sys_prompt = f"Gorev tipi: {task['type']}."
                
                try:
                    ai_answer = ask_openrouter(sys_prompt, user_prompt, model)
                except Exception as e:
                    ai_answer = str(e)
                
                if task["type"] == "media":
                    handle_media_request(ai_answer, chat_id, doc_id)
                
                request_data["status"] = "completed"
                request_data["answer"] = ai_answer
                request_data["completed_time"] = datetime.now(timezone.utc)
                request_data["model_used"] = model
                
                db.delete_document("islenmis_istekler", doc_id)
                db.create_document_with_id("tamamlanmis_istekler", doc_id, request_data)
                
                if task["type"] != "media" and chat_id:
                    try:
                        bot.send_message(chat_id, ai_answer)
                    except:
                        pass
                        
        except:
            pass
            
        time.sleep(3)

if __name__ == "__main__":
    start_worker()
