"""Human-entered masked credentials; pairing binds only an explicit private chat."""
import queue
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser
from . import telegram


class TelegramSettings:
    def __init__(self,parent=None):
        self.window=tk.Toplevel(parent) if parent else tk.Tk()
        self.window.title('텔레그램 알림 연결')
        self.window.geometry('640x460')
        self.window.minsize(600,440)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.closed=False
        self.busy=False
        self.pair=None
        self.queue=queue.Queue()
        box=ttk.Frame(self.window,padding=24)
        box.pack(fill='both',expand=True)
        ttk.Label(box,text='휴대폰으로 좌석 알림 받기',font=('맑은 고딕',18,'bold')).pack(anchor='w',pady=(0,16))
        ttk.Label(box,text='1. BotFather에서 받은 봇 토큰을 입력하세요.\n토큰은 Windows 자격 증명 저장소에 보관합니다.',font=('맑은 고딕',10)).pack(anchor='w')
        self.token=tk.StringVar()
        self.entry=ttk.Entry(box,textvariable=self.token,show='●',font=('맑은 고딕',12))
        self.entry.pack(fill='x',pady=12,ipady=6)
        self.verify=ttk.Button(box,text='토큰 확인 및 안전하게 저장',command=self.begin)
        self.verify.pack(anchor='w')
        ttk.Separator(box).pack(fill='x',pady=20)
        ttk.Label(box,text='2. 봇 대화를 열고 시작을 누른 뒤 연결을 확인하세요.',font=('맑은 고딕',10)).pack(anchor='w')
        row=ttk.Frame(box)
        row.pack(fill='x',pady=12)
        self.open=ttk.Button(row,text='내 봇 대화 열기',state='disabled',command=self.open_chat)
        self.open.pack(side='left',padx=(0,8))
        self.confirm=ttk.Button(row,text='연결 확인 · 테스트 알림 보내기',state='disabled',command=self.finish)
        self.confirm.pack(side='left')
        self.status=tk.StringVar(value='이미 연결됨' if telegram.config().get('enabled') else '토큰을 채팅에 보내지 말고 위 입력란에 붙여 넣어 주세요.')
        ttk.Label(box,textvariable=self.status,wraplength=570,font=('맑은 고딕',10)).pack(anchor='w',pady=12)
        self.window.after(150,self.poll)

    def run(self,kind,fn):
        if self.busy:
            return
        self.busy=True
        self.verify.configure(state='disabled')
        self.confirm.configure(state='disabled')
        def work():
            try:
                self.queue.put((kind,fn(),None))
            except Exception as exc:
                self.queue.put((kind,None,str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def begin(self):
        token=self.token.get()
        self.token.set('')
        self.status.set('봇 토큰을 확인하고 있습니다…')
        self.run('pair',lambda:telegram.begin_pairing(token))

    def open_chat(self):
        if self.pair:
            webbrowser.open('https://t.me/'+self.pair['username']+'?start='+self.pair['code'])

    def finish(self):
        if self.pair:
            self.status.set('연결 메시지를 확인하고 테스트 알림을 보냅니다…')
            self.run('done',lambda:telegram.finish_pairing(self.pair))

    def poll(self):
        if self.closed:
            return
        try:
            kind,result,error=self.queue.get_nowait()
        except queue.Empty:
            self.window.after(150,self.poll)
            return
        self.busy=False
        self.verify.configure(state='normal')
        if error:
            self.status.set(error)
        elif kind=='pair':
            self.pair=result
            self.open.configure(state='normal')
            self.status.set('@'+result['username']+' 확인 완료. 내 봇 대화 열기 → 시작 → 연결 확인 순서로 진행하세요.')
        else:
            self.status.set('연결 완료 · 텔레그램이 테스트 알림을 수락했습니다. 휴대폰에서 수신을 확인해 주세요.')
        self.confirm.configure(state='normal' if self.pair else 'disabled')
        self.window.after(150,self.poll)

    def close(self):
        self.token.set('')
        self.closed=True
        self.window.destroy()


if __name__=='__main__':
    dialog=TelegramSettings()
    dialog.window.mainloop()
