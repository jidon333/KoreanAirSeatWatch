"""Small Windows UI: one condition, explicit search, optional local monitoring."""
from dataclasses import asdict
from datetime import datetime, date, timedelta
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk
from .model import Condition
from .session import ROOT, atomic_json
from .worker_client import execute

# Airport departure-board palette; one route row leads, results follow.
COLORS = {'paper':'#F6F8FA', 'ink':'#102B4B', 'muted':'#627388',
          'line':'#DCE4EC', 'sky':'#176B9C', 'ok':'#17684E', 'error':'#A23A31', 'white':'#FFFFFF'}


class App:
    def __init__(self, root, executor=execute):
        self.root, self.executor = root, executor
        self.messages = queue.Queue()
        self.busy = False
        self.monitoring = False
        self.timer = None
        self.closed = False
        self.current = None
        self.last_result = None
        self.known = {}
        root.title('대한항공 좌석 알림')
        root.geometry('940x700')
        root.minsize(860, 680)
        root.configure(bg=COLORS['paper'])
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('맑은 고딕', 11), background=COLORS['paper'], foreground=COLORS['ink'])
        style.configure('TButton', padding=(16, 10), bordercolor=COLORS['line'])
        style.configure('Primary.TButton', foreground=COLORS['white'], background=COLORS['sky'])
        style.map('Primary.TButton', background=[('disabled', '#ABBBC9'), ('active', '#135C87')])
        style.configure('TEntry', padding=8, fieldbackground=COLORS['white'])
        style.configure('TCombobox', padding=8, fieldbackground=COLORS['white'])
        style.configure('Treeview', rowheight=44, background=COLORS['white'], fieldbackground=COLORS['white'], borderwidth=0)
        style.configure('Treeview.Heading', font=('맑은 고딕',10,'bold'), padding=10)
        wrap = ttk.Frame(root, padding=32)
        wrap.pack(fill='both', expand=True)
        head = ttk.Frame(wrap)
        head.pack(fill='x')
        ttk.Label(head, text='마일리지 좌석 찾기', font=('맑은 고딕',22,'bold')).pack(side='left')
        self.browser_button = ttk.Button(head, text='대한항공 창 / 로그인', command=self.open_browser)
        self.browser_button.pack(side='right')
        ttk.Label(wrap, text='편도 · 로그인한 회원 1명 · 가까운 날짜 함께 조회', foreground=COLORS['muted']).pack(anchor='w', pady=(8,24))
        form = ttk.Frame(wrap)
        form.pack(fill='x')
        defaults = {'origin':'ICN', 'destination':'ORD', 'departure':(date.today()+timedelta(days=3)).isoformat(), 'cabin':'프레스티지석'}
        try:
            defaults.update(json.loads((ROOT/'data'/'ui-condition.json').read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
        self.fields, self.inputs = {}, []
        for i, (name,label,width) in enumerate([('origin','출발 공항',12), ('destination','도착 공항',12), ('departure','출발일 · YYYY-MM-DD',22), ('cabin','찾을 좌석 등급',18)]):
            cell = ttk.Frame(form)
            cell.grid(row=0,column=i,sticky='ew',padx=(0,16 if i<3 else 0))
            form.columnconfigure(i,weight=1)
            ttk.Label(cell,text=label,foreground=COLORS['muted'],font=('맑은 고딕',10)).pack(anchor='w',pady=(0,8))
            value = tk.StringVar(value=defaults[name])
            self.fields[name]=value
            widget = (ttk.Combobox(cell,textvariable=value,values=('일반석','프리미엄석','프레스티지석','일등석'),state='readonly',width=width)
                      if name=='cabin' else ttk.Entry(cell,textvariable=value,width=width))
            widget.pack(fill='x')
            self.inputs.append(widget)
        ttk.Label(wrap,text='공항 코드 예: ICN 인천 · ORD 시카고 · ATL 애틀랜타',foreground=COLORS['muted'],font=('맑은 고딕',10)).pack(anchor='w',pady=(8,16))
        actions = ttk.Frame(wrap)
        actions.pack(fill='x')
        self.search_button = ttk.Button(actions,text='지금 조회',style='Primary.TButton',command=self.search)
        self.search_button.pack(side='left')
        self.monitor_button = ttk.Button(actions,text='15분마다 확인',command=self.toggle_monitor)
        self.monitor_button.pack(side='left',padx=8)
        self.status = tk.StringVar(value='조건을 입력하고 조회해 주세요.')
        self.status_label = ttk.Label(wrap,textvariable=self.status,wraplength=760,font=('맑은 고딕',11,'bold'))
        self.status_label.pack(fill='x',pady=(24,8))
        self.result_caption = tk.StringVar(value='아직 확인한 좌석이 없습니다.')
        ttk.Label(wrap,textvariable=self.result_caption,foreground=COLORS['muted'],wraplength=760,font=('맑은 고딕',10)).pack(anchor='w',pady=(0,12))
        self.table=ttk.Treeview(wrap,columns=('flight','cabin','miles','seats','status'),show='headings',height=3,selectmode='none')
        for key,label,width in [('flight','항공편',100),('cabin','등급',170),('miles','마일리지',150),('seats','잔여 좌석',140),('status','조회 결과',200)]:
            self.table.heading(key,text=label)
            self.table.column(key,width=width,anchor='center')
        self.table.tag_configure('available',foreground=COLORS['ok'])
        self.table.pack(fill='both',expand=True)
        ttk.Label(wrap,text='자동 확인 중 새 좌석은 이 창과 알림음으로 알려드립니다.\n로그인 만료·조회 오류 시 자동 확인을 중단합니다. 예약은 대한항공에서 진행해 주세요.',foreground=COLORS['muted'],font=('맑은 고딕',10)).pack(anchor='w',pady=(16,0))
        try:
            saved=json.loads((ROOT/'data'/'last-success.json').read_text(encoding='utf-8'))
            if saved.get('state')=='OK':
                self.render(saved, saved=True)
        except (OSError, ValueError, KeyError):
            pass
        root.after(150,self.poll)

    def condition(self):
        values={key:var.get().strip() for key,var in self.fields.items()}
        values['origin']=values['origin'].upper()
        values['destination']=values['destination'].upper()
        condition=Condition(**values)
        if not 0 <= (date.fromisoformat(condition.departure)-date.today()).days <= 360:
            raise ValueError('출발일은 오늘부터 360일 이내로 입력해 주세요.')
        return condition

    def update_controls(self):
        locked=self.busy or self.monitoring
        for field in self.inputs:
            field.configure(state='disabled' if locked else ('readonly' if isinstance(field,ttk.Combobox) else 'normal'))
        self.search_button.configure(state='disabled' if locked else 'normal')
        self.browser_button.configure(state='disabled' if self.busy else 'normal')
        self.monitor_button.configure(text='자동 확인 중지' if self.monitoring else '15분마다 확인',state='disabled' if self.busy and not self.monitoring else 'normal')

    def dispatch(self,step,payload=None):
        if self.busy:
            return
        self.busy=True
        self.update_controls()
        def work():
            try:
                self.messages.put((step,self.executor(step,payload),None))
            except Exception as exc:
                self.messages.put((step,None,str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def search(self):
        if self.busy:
            return
        try:
            self.current=self.condition()
        except ValueError as exc:
            self.status.set(str(exc))
            self.status_label.configure(foreground=COLORS['error'])
            self.stop_monitor()
            return
        atomic_json(ROOT/'data'/'ui-condition.json',asdict(self.current))
        self.status.set('조회 중입니다. 대한항공 창을 준비하고 실제 항공편을 확인합니다…')
        self.status_label.configure(foreground=COLORS['ink'])
        self.dispatch('query_condition',{'condition':asdict(self.current)})

    def open_browser(self):
        self.stop_monitor()
        self.status.set('대한항공 창을 여는 중입니다…')
        self.dispatch('focus_browser')

    def toggle_monitor(self):
        if self.monitoring:
            self.stop_monitor()
            self.status.set('자동 확인을 중지했습니다.'+(' 진행 중인 조회는 마무리합니다.' if self.busy else ''))
        else:
            self.monitoring=True
            self.search()

    def stop_monitor(self):
        self.monitoring=False
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer=None
        self.update_controls()

    def render(self,result,saved=False):
        self.last_result=result
        self.table.delete(*self.table.get_children())
        c=Condition(**result['condition'])
        checked=datetime.fromisoformat(result['checked_at']).astimezone().strftime('%m월 %d일 %H:%M:%S')
        self.result_caption.set(c.label+'  |  '+('저장된 정상 결과' if saved else '정상 확인')+' '+checked)
        for row in result['rows']:
            self.table.insert('', 'end',values=(row['flight'],row['cabin'],f"{row['miles']:,}" if row['miles'] else '—',
                str(row['seats'])+'석' if row['seats'] is not None else '수량 미표시', '예약 가능' if row['available'] else row['detail']),tags=('available',) if row['available'] else ())
        current={row['flight'] for row in result['rows'] if row['available']}
        appeared=current-self.known.get(c.key,set())
        self.known[c.key]=current
        if not saved and self.monitoring and appeared:
            self.root.bell()
            self.root.title('좌석 발견! · 대한항공 좌석 알림')

    def poll(self):
        if self.closed:
            return
        try:
            step,result,error=self.messages.get_nowait()
        except queue.Empty:
            self.root.after(150,self.poll)
            return
        self.busy=False
        if error or (step=='query_condition' and result.get('state')!='OK') or (result and result.get('state')=='CLOSED'):
            self.stop_monitor()
            self.status.set((error or result.get('message','조회를 확인하지 못했습니다.'))+' · 마지막 정상 결과는 아래에 유지합니다.')
            self.status_label.configure(foreground=COLORS['error'])
        elif step=='query_condition':
            self.render(result)
            self.status.set(('좌석을 찾았습니다.' if any(r['available'] for r in result['rows']) else '선택한 등급은 매진입니다.')+
                            (' 15분 뒤 다시 확인합니다.' if self.monitoring else ''))
            self.status_label.configure(foreground=COLORS['ok'])
            if self.monitoring:
                self.timer=self.root.after(15*60*1000,self.search)
        else:
            self.status.set('대한항공 창을 열었습니다. 필요한 경우 로그인한 뒤 지금 조회를 눌러 주세요.')
        self.update_controls()
        self.root.after(150,self.poll)

    def close(self):
        self.closed=True
        self.stop_monitor()
        self.root.destroy()


def main():
    from .single_instance import SingleInstance
    with SingleInstance('KoreanAirSeatWatch_UI'):
        root=tk.Tk()
        App(root)
        root.mainloop()


if __name__=='__main__':
    main()
