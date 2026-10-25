"""Windows departure-board UI with a serial, persistent search queue."""
from dataclasses import asdict
from datetime import datetime, date, timedelta
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from .model import Condition
from .session import ROOT, atomic_json
from .worker_client import execute
from .monitor import Monitor
from .search_queue import SearchQueue

# Intent: a couple tracks several award trips without watching the browser.
# Hierarchy: route queue leads; selected flight details sit underneath.
# Domain: departure, destination, cabin, available seats, next departure/check.
# Palette: navy signage, pale terminal walls, sky blue action, green availability.
# Depth/surfaces: existing quiet paper and white rows, light separators, no cards.
# Type: Malgun Gothic 22 bold title, 11 body, 10 muted metadata; 8px rhythm.
# Signature: the next route is marked in the queue with its global wait time.
# Avoid: repeated result cards, per-route timers, and a dashboard of counters.
COLORS = {'paper':'#F6F8FA', 'ink':'#102B4B', 'muted':'#627388',
          'line':'#DCE4EC', 'sky':'#176B9C', 'ok':'#17684E', 'error':'#A23A31', 'white':'#FFFFFF'}


class App:
    def __init__(self, root, executor=execute, monitor=None):
        self.root, self.executor = root, executor
        self.messages = queue.Queue()
        self.busy = False
        self.monitoring = False
        self.timer = None
        self.closed = False
        self.current = None
        self.inflight_monitor = False
        self.last_result = None
        self.known = {}
        self.monitor = monitor or Monitor(ROOT/'data'/'monitor.json')
        self.notifying = False
        self.next_notification = 0
        self.settings = None
        root.title('대한항공 좌석 알림')
        root.geometry('1040x850')
        root.minsize(980, 800)
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
        style.configure('Queue.Treeview', rowheight=34)
        style.configure('Treeview.Heading', font=('맑은 고딕',10,'bold'), padding=10)
        wrap = ttk.Frame(root, padding=24)
        wrap.pack(fill='both', expand=True)
        head = ttk.Frame(wrap)
        head.pack(fill='x')
        ttk.Label(head, text='마일리지 좌석 찾기', font=('맑은 고딕',22,'bold')).pack(side='left')
        self.browser_button = ttk.Button(head, text='대한항공 창 / 로그인', command=self.open_browser)
        self.browser_button.pack(side='right')
        ttk.Label(wrap, text='편도 · 로그인한 회원 1명 · 가까운 날짜 함께 조회', foreground=COLORS['muted']).pack(anchor='w', pady=(8,16))
        form = ttk.Frame(wrap)
        form.pack(fill='x')
        defaults = {'origin':'ICN', 'destination':'ORD', 'departure':(date.today()+timedelta(days=3)).isoformat(), 'cabin':'프레스티지석'}
        try:
            defaults.update(json.loads((ROOT/'data'/'ui-condition.json').read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
        self.search_queue = SearchQueue(ROOT/'data'/'search-queue.json', Condition(**defaults))
        self.monitor.interval = self.search_queue.interval
        if self.search_queue.next_condition:
            defaults = asdict(self.search_queue.next_condition)
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
        edit = ttk.Frame(wrap)
        edit.pack(fill='x', pady=(8,12))
        self.add_button = ttk.Button(edit,text='조건 추가',command=self.add_condition)
        self.add_button.pack(side='left')
        self.edit_button = ttk.Button(edit,text='선택 조건 수정',command=lambda:self.add_condition(replace=True))
        self.edit_button.pack(side='left',padx=8)
        self.remove_button = ttk.Button(edit,text='삭제',command=self.remove_condition)
        self.remove_button.pack(side='left')
        ttk.Label(edit,text='공항 코드: ICN 인천 · ORD 시카고 · ATL 애틀랜타',foreground=COLORS['muted'],font=('맑은 고딕',10)).pack(side='right')
        list_frame = ttk.Frame(wrap)
        list_frame.pack(fill='x')
        self.queue_table = ttk.Treeview(list_frame,style='Queue.Treeview',columns=('order','route','date','cabin','state'),show='headings',height=3,selectmode='browse')
        for key,label,width in [('order','순서',65),('route','노선',150),('date','출발일',130),('cabin','등급',130),('state','마지막 조회 상태',360)]:
            self.queue_table.heading(key,text=label)
            self.queue_table.column(key,width=width,anchor='center',stretch=key=='state')
        self.queue_table.pack(side='left',fill='x',expand=True)
        scroll=ttk.Scrollbar(list_frame,orient='vertical',command=self.queue_table.yview)
        scroll.pack(side='right',fill='y')
        self.queue_table.configure(yscrollcommand=scroll.set)
        self.queue_table.bind('<<TreeviewSelect>>',self.select_condition)
        pacing=ttk.Frame(wrap)
        pacing.pack(fill='x',pady=(12,12))
        ttk.Label(pacing,text='전체 조회 간격').pack(side='left')
        self.interval_var=tk.StringVar(value=str(self.search_queue.interval//60))
        self.interval_input=ttk.Spinbox(pacing,from_=5,to=60,width=4,textvariable=self.interval_var,command=self.apply_interval)
        self.interval_input.pack(side='left',padx=8)
        self.interval_input.bind('<FocusOut>',lambda event:self.apply_interval())
        self.interval_input.bind('<Return>',lambda event:self.apply_interval())
        ttk.Label(pacing,text='분 · 한 번에 조건 하나씩',foreground=COLORS['muted']).pack(side='left')
        self.queue_hint=tk.StringVar()
        ttk.Label(pacing,textvariable=self.queue_hint,foreground=COLORS['muted'],font=('맑은 고딕',10)).pack(side='right')
        actions = ttk.Frame(wrap)
        actions.pack(fill='x')
        self.search_button = ttk.Button(actions,text='입력 조건 조회',style='Primary.TButton',command=self.search)
        self.search_button.pack(side='left')
        self.monitor_button = ttk.Button(actions,text='순차 자동 확인',command=self.toggle_monitor)
        self.monitor_button.pack(side='left',padx=8)
        ttk.Button(actions,text='텔레그램 연결',command=self.open_telegram).pack(side='right')
        self.status = tk.StringVar(value='조건을 입력하고 조회해 주세요.')
        self.status_label = ttk.Label(wrap,textvariable=self.status,wraplength=760,font=('맑은 고딕',11,'bold'))
        self.status_label.pack(fill='x',pady=(16,8))
        self.result_caption = tk.StringVar(value='아직 확인한 좌석이 없습니다.')
        ttk.Label(wrap,textvariable=self.result_caption,foreground=COLORS['muted'],wraplength=760,font=('맑은 고딕',10)).pack(anchor='w',pady=(0,12))
        self.table=ttk.Treeview(wrap,columns=('flight','cabin','miles','seats','status'),show='headings',height=3,selectmode='none')
        for key,label,width in [('flight','항공편',100),('cabin','등급',170),('miles','마일리지',150),('seats','잔여 좌석',140),('status','조회 결과',200)]:
            self.table.heading(key,text=label)
            self.table.column(key,width=width,anchor='center')
        self.table.tag_configure('available',foreground=COLORS['ok'])
        self.table.pack(fill='both',expand=True)
        self.notification_status = tk.StringVar(value='텔레그램 연결 상태 확인 중…')
        ttk.Label(wrap,textvariable=self.notification_status,foreground=COLORS['muted'],font=('맑은 고딕',10)).pack(anchor='w',pady=(16,0))
        ttk.Label(wrap,text='이 앱과 PC가 켜져 있어야 자동 확인합니다. 로그인 만료 시 재로그인 후 재개해 주세요.\n통신 오류는 제한적으로 재시도하며, 절전에서 돌아오면 밀린 조회는 한 번만 실행합니다.',foreground=COLORS['muted'],font=('맑은 고딕',10)).pack(anchor='w',pady=(6,0))
        try:
            saved=json.loads((ROOT/'data'/'last-success.json').read_text(encoding='utf-8'))
            c=Condition(**saved['condition'])
            if saved.get('state')=='OK' and c in self.search_queue.conditions and not self.search_queue.result(c):
                self.search_queue.data['results'][c.key]={'state':'OK','checked_at':saved['checked_at'],'success':saved}
                finished=datetime.fromisoformat(saved['checked_at']).timestamp()
                self.search_queue.data['last_finished']=max(self.search_queue.data.get('last_finished',0),finished)
                self.search_queue.data['not_before']=max(self.search_queue.not_before,finished+self.search_queue.interval)
                self.search_queue.save()
        except (OSError, ValueError, KeyError):
            pass
        self.refresh_queue()
        if self.search_queue.next_condition:
            self.queue_table.selection_set(self.search_queue.next_condition.key)
            self.select_condition()
        root.after(150,self.poll)
        self.update_controls()
        root.after(1000,self.tick)

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
        for widget in (self.add_button,self.edit_button,self.remove_button,self.interval_input):
            widget.configure(state='disabled' if locked else 'normal')
        self.monitor_button.configure(text='자동 확인 중지' if self.monitoring else ('자동 확인 재개' if self.monitor.paused else '순차 자동 확인'),state='disabled' if self.busy and not self.monitoring else 'normal')

    def apply_interval(self):
        if self.busy or self.monitoring:
            return True
        try:
            self.search_queue.set_interval(int(self.interval_var.get()))
            self.monitor.interval=self.search_queue.interval
            self.refresh_queue()
            return True
        except ValueError:
            self.status.set('전체 조회 간격은 5~60분의 정수로 입력해 주세요.')
            return False

    def add_condition(self, replace=False):
        if self.busy or self.monitoring:
            return
        try:
            c=self.condition()
            selected=self.queue_table.selection()
            if replace and not selected:
                raise ValueError('수정할 조건을 목록에서 선택해 주세요.')
            self.search_queue.put(c, selected[0] if replace else None)
            if replace and selected[0]!=c.key:
                self.monitor.discard_pending(selected[0])
            self.refresh_queue()
            self.queue_table.selection_set(c.key)
            self.status.set('조건을 저장했습니다. 순차 자동 확인을 시작해 주세요.')
        except ValueError as exc:
            self.status.set(str(exc))

    def remove_condition(self):
        selected=self.queue_table.selection()
        if self.busy or self.monitoring or not selected:
            return
        self.search_queue.remove(selected[0])
        self.monitor.discard_pending(selected[0])
        self.refresh_queue()
        if self.search_queue.next_condition:
            self.queue_table.selection_set(self.search_queue.next_condition.key)
        else:
            self.table.delete(*self.table.get_children())
            self.result_caption.set('등록된 조건이 없습니다. 위에서 조건을 추가해 주세요.')

    def select_condition(self,event=None):
        selected=self.queue_table.selection()
        if not selected:
            return
        c=next((x for x in self.search_queue.conditions if x.key==selected[0]),None)
        if not c:
            return
        if not self.busy and not self.monitoring:
            for key,var in self.fields.items():
                var.set(getattr(c,key))
        saved=self.search_queue.result(c).get('success')
        if saved:
            self.render(saved,saved=True)
        else:
            self.table.delete(*self.table.get_children())
            self.result_caption.set(c.label+' | 아직 정상 조회 결과가 없습니다.')

    def refresh_queue(self):
        keys={c.key for c in self.search_queue.conditions}
        for key in self.queue_table.get_children():
            if key not in keys:
                self.queue_table.delete(key)
        next_c=self.search_queue.next_condition
        labels={'OK':'정상','LOGIN_REQUIRED':'로그인 필요','BLOCKED':'접근 거부','NETWORK_ERROR':'통신 오류','CLOSED':'창 닫힘','UNVERIFIED':'확인 실패','UI_CHANGED':'화면 확인 실패'}
        for i,c in enumerate(self.search_queue.conditions):
            record=self.search_queue.result(c)
            state=labels.get(record.get('state'),'미조회')
            if record.get('state')=='OK':
                state='예약 가능' if any(r['available'] for r in record['success']['rows']) else '매진'
            if record.get('checked_at'):
                state+=' · '+datetime.fromisoformat(record['checked_at']).astimezone().strftime('%m/%d %H:%M')
            values=(('다음 ' if next_c==c else '')+str(i+1),f'{c.origin} → {c.destination}',c.departure,c.cabin,state)
            if self.queue_table.exists(c.key):
                self.queue_table.item(c.key,values=values)
                self.queue_table.move(c.key,'',i)
            else:
                self.queue_table.insert('','end',iid=c.key,values=values)
        count=len(self.search_queue.conditions)
        hint=f'{count}개 조건 · 각 조건 약 {count*self.search_queue.interval//60}분마다'
        due=self.monitor.due_at if self.monitoring else self.search_queue.not_before
        if due and due>time.time():
            hint+=' · 다음 '+datetime.fromtimestamp(due).strftime('%H:%M:%S')
        self.queue_hint.set(hint)

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

    def search(self, scheduled=False):
        if self.busy or (self.monitoring and not scheduled):
            return
        try:
            self.current=self.search_queue.next_condition if scheduled else self.condition()
            if self.current is None:
                raise ValueError('조회할 조건을 먼저 추가해 주세요.')
            if not 0 <= (date.fromisoformat(self.current.departure)-date.today()).days <= 360:
                raise ValueError(self.current.label+' · 출발일을 오늘부터 360일 이내로 수정해 주세요.')
        except ValueError as exc:
            self.status.set(str(exc))
            self.status_label.configure(foreground=COLORS['error'])
            self.stop_monitor()
            return
        if time.time()<self.search_queue.not_before:
            self.status.set('요청 간격을 지키기 위해 '+datetime.fromtimestamp(self.search_queue.not_before).strftime('%H:%M:%S')+' 이후 조회할 수 있습니다.')
            if scheduled:
                self.monitor.due_at=self.search_queue.not_before
                self.monitor.save()
            return
        self.inflight_monitor=scheduled
        self.search_queue.started()
        atomic_json(ROOT/'data'/'ui-condition.json',asdict(self.current))
        self.status.set(self.current.label+' · 조회 중…')
        self.status_label.configure(foreground=COLORS['ink'])
        self.dispatch('query_condition',{'condition':asdict(self.current)})

    def open_browser(self):
        self.stop_monitor(paused=self.monitoring or self.monitor.paused)
        self.status.set('대한항공 창을 여는 중입니다…')
        self.dispatch('focus_browser')

    def open_telegram(self):
        from .telegram_ui import TelegramSettings
        if self.settings and not self.settings.closed:
            self.settings.window.lift()
        else:
            self.settings=TelegramSettings(self.root)

    def toggle_monitor(self):
        if self.monitoring:
            self.stop_monitor()
            self.status.set('자동 확인을 중지했습니다.'+(' 진행 중인 조회는 마무리합니다.' if self.busy else ''))
        else:
            if not self.search_queue.conditions:
                self.status.set('조회할 조건을 먼저 추가해 주세요.')
                return
            if not self.apply_interval():
                return
            self.monitor.start(not_before=self.search_queue.not_before)
            self.monitoring=True
            if self.monitor.take_due():
                self.search(scheduled=True)
            else:
                self.status.set('순차 자동 확인을 시작했습니다. 다음 조회 시각을 기다립니다.')
            self.update_controls()
            self.refresh_queue()

    def stop_monitor(self, paused=False):
        self.monitoring=False
        self.monitor.stop(paused=paused)
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer=None
        self.update_controls()
        if not self.busy:
            self.select_condition()

    def tick(self):
        if self.closed:
            return
        if not self.busy and self.monitor.take_due():
            self.search(scheduled=True)
        self.refresh_queue()
        if not self.notifying and time.time() >= self.next_notification:
            self.notifying=True
            def deliver():
                try:
                    self.monitor.flush()
                finally:
                    self.messages.put(('notification',{},None))
            threading.Thread(target=deliver,daemon=True).start()
        self.root.after(1000,self.tick)

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
        if step=='notification':
            self.notifying=False
            self.next_notification=time.time()+30
            self.notification_status.set(self.monitor.delivery_status)
            self.root.after(150,self.poll)
            return
        self.busy=False
        monitoring_message=''
        if step=='query_condition' and self.current:
            monitored=result if not error else {'state':'UNVERIFIED','message':error}
            self.search_queue.finished(self.current,monitored,advance=self.inflight_monitor)
        if step=='query_condition' and self.monitoring:
            monitored=result if not error else {'state':'UNVERIFIED','message':error}
            monitoring_message=self.monitor.accept(self.current,monitored)
            self.monitoring=self.monitor.active
            if self.monitor.due_at:
                self.search_queue.data['not_before']=max(self.search_queue.not_before,self.monitor.due_at)
                self.search_queue.save()
            self.next_notification=0
        self.refresh_queue()
        if error or (step=='query_condition' and result.get('state')!='OK') or (result and result.get('state')=='CLOSED'):
            self.status.set((monitoring_message or error or result.get('message','조회를 확인하지 못했습니다.'))+' · 마지막 정상 결과는 아래에 유지합니다.')
            self.status_label.configure(foreground=COLORS['error'])
        elif step=='query_condition':
            self.render(result)
            c=Condition(**result['condition'])
            if self.queue_table.exists(c.key):
                self.queue_table.selection_set(c.key)
            self.status.set(('좌석을 찾았습니다.' if any(r['available'] for r in result['rows']) else '선택한 등급은 매진입니다.')+
                            (' '+monitoring_message if monitoring_message else ''))
            self.status_label.configure(foreground=COLORS['ok'])
        else:
            self.status.set('대한항공 창을 열었습니다. 로그인한 뒤 '+('자동 확인 재개' if self.monitor.paused else '입력 조건 조회')+'를 눌러 주세요.')
        self.update_controls()
        self.root.after(150,self.poll)

    def close(self):
        self.closed=True
        self.stop_monitor(paused=self.monitoring or self.monitor.paused)
        self.root.destroy()


def main():
    from .single_instance import SingleInstance
    with SingleInstance('KoreanAirSeatWatch_UI'):
        root=tk.Tk()
        App(root)
        root.mainloop()


if __name__=='__main__':
    main()
