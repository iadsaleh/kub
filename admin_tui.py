from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog, Static
import asyncio
from datetime import datetime

class NexusAdmin(App):
    CSS = "RichLog { background: black; color: lime; border: double lime; height: 1fr; }"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("📈 [KUN 0X NEXUS] LIVE TRAFFIC MONITOR", id="title")
        yield RichLog(id="logs", max_lines=500, wrap=True, highlight=True)
        yield Footer()

    async def action_add_log(self, message: str):
        """دالة مخصصة لإضافة سجل للمربع من خارج الـ App"""
        log_widget = self.query_one("#logs")
        log_widget.write(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    # هذه الدالة ستستمع للرسائل القادمة من البوت
    async def log_worker(self, queue: asyncio.Queue):
        while True:
            message = await queue.get()
            self.query_one("#logs").write(message)
            queue.task_done()
