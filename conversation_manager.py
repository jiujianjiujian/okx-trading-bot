"""
共享会话管理器 — QQ与终端共享对话上下文

将对话历史持久化到 JSON 文件，供不同接口共享访问。
"""

import json
import os
import threading
import time


class ConversationManager:
    """线程安全的对话持久化管理器"""

    def __init__(self, db_path: str = "conversations.json", max_history: int = 20):
        self.db_path = db_path
        self.max_history = max_history
        self._lock = threading.Lock()
        self._data: dict = self._load()
        self._last_save = 0.0

    def _load(self) -> dict:
        if not os.path.exists(self.db_path):
            return {}
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, IOError) as e:
            print(f"[会话] 读取对话文件失败: {e}")
            return {}

    def _save(self):
        now = time.time()
        if now - self._last_save < 0.5:
            return
        tmp_path = self.db_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.db_path)
            self._last_save = now
        except Exception as e:
            print(f"[会话] 保存对话失败: {e}")

    def get_conversation(self, user_id: str) -> list:
        with self._lock:
            return list(self._data.get(user_id, []))

    def add_message(self, user_id: str, role: str, content: str):
        with self._lock:
            if user_id not in self._data:
                self._data[user_id] = []
            self._data[user_id].append({
                "role": role,
                "content": content,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            if len(self._data[user_id]) > self.max_history:
                self._data[user_id] = self._data[user_id][-self.max_history:]
        self._save()

    def clear_conversation(self, user_id: str):
        with self._lock:
            self._data.pop(user_id, None)
        self._save()

    def build_claude_context(self, user_id: str) -> list:
        """构建适合 Anthropic API 的消息列表（仅 role + content）"""
        messages = self.get_conversation(user_id)
        return [{"role": m["role"], "content": m["content"]} for m in messages]

    def force_save(self):
        """强制保存（关闭时调用）"""
        with self._lock:
            try:
                tmp_path = self.db_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self.db_path)
            except Exception as e:
                print(f"[会话] 强制保存失败: {e}")
