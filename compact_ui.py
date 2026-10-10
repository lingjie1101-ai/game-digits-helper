"""Compact native Tk layout; low-frequency options live in a nonmodal panel."""
import tkinter as tk
from tkinter import ttk
from core import KEYWORDS

def build_tab(s):
    s.settings_dialog=None;s.profile_combo=None
    s.host.columnconfigure(0,weight=1);s.host.rowconfigure(1,weight=1)
    connection=ttk.Frame(s.host,padding=(12,9,12,6));connection.grid(row=0,column=0,sticky='ew')
    connection.columnconfigure(0,weight=1)
    s.window_combo=ttk.Combobox(connection,state='readonly');s.window_combo.grid(row=0,column=0,sticky='ew')
    ttk.Button(connection,text='刷新',width=4,command=s.refresh_windows).grid(row=0,column=1,padx=6)
    s.connect_button=ttk.Button(connection,text='连接窗口',width=7,style='Accent.TButton',command=s.connect)
    s.connect_button.grid(row=0,column=2)
    body=ttk.Frame(s.host,padding=(12,4,12,0));body.grid(row=1,column=0,sticky='nsew')
    body.columnconfigure(0,weight=1);body.rowconfigure(0,weight=1)
    preview=ttk.Frame(body);preview.grid(row=0,column=0,sticky='nsew',padx=(0,12))
    s.preview_panel=preview;s.body_panel=body
    if s.hub.mini:
        preview.grid_remove();body.columnconfigure(0,weight=0);body.columnconfigure(1,weight=1)
    preview.columnconfigure(0,weight=1);preview.rowconfigure(1,weight=1)
    toolbar=ttk.Frame(preview);toolbar.grid(row=0,column=0,sticky='ew',pady=(0,5))
    ttk.Label(toolbar,text='实时预览',font=('Microsoft YaHei',9,'bold')).pack(side='left')
    s.cancel_button=ttk.Button(toolbar,text='取消框选',command=s.cancel_selection,state='disabled');s.cancel_button.pack(side='right')
    ttk.Button(toolbar,text='识别整窗',command=s.full_region).pack(side='right',padx=4)
    ttk.Button(toolbar,text='选识别区',command=s.select_ocr_region).pack(side='right')
    s.canvas=tk.Canvas(preview,bg='#e8efea',highlightthickness=1,highlightbackground='#d9e3dc',cursor='arrow',height=320,width=440)
    s.canvas.grid(row=1,column=0,sticky='nsew')
    s.canvas.bind('<ButtonPress-1>',s.selection_press);s.canvas.bind('<B1-Motion>',s.selection_drag)
    s.canvas.bind('<ButtonRelease-1>',s.selection_release);s.canvas.bind('<Escape>',lambda event:s.cancel_selection())
    s.canvas.bind('<Configure>',lambda event:s.render())
    s.region_text=tk.StringVar(value='默认识别整个窗口')
    ttk.Label(preview,textvariable=s.region_text,foreground='#798477',font=('Microsoft YaHei',8)).grid(row=2,column=0,sticky='w',pady=(5,4))
    s.selection_hint=tk.StringVar(value='自动定位确认弹窗输入框；橙框内的游戏左键点击触发粘贴。')
    hint=ttk.Label(preview,textvariable=s.selection_hint,wraplength=550,foreground='#9b4b0e',font=('Microsoft YaHei',8))
    hint.grid(row=3,column=0,sticky='ew',pady=(5,0))
    preview.bind('<Configure>',lambda event:hint.configure(wraplength=max(180,event.width)))

    side=ttk.Frame(body,width=238);side.grid(row=0,column=1,sticky='ns');side.columnconfigure(0,weight=1)
    digits=ttk.LabelFrame(side,text='确认数字',padding=(10,7));digits.grid(row=0,column=0,sticky='ew')
    digits.columnconfigure(0,weight=1)
    s.phase=tk.StringVar(value='启动中')
    s.phase_label=ttk.Label(digits,textvariable=s.phase,foreground='#a76d11',font=('Microsoft YaHei',9,'bold'))
    s.phase_label.grid(row=0,column=0,columnspan=2,sticky='w')
    s.result=tk.StringVar(value='— — — —')
    ttk.Label(digits,textvariable=s.result,font=('Consolas',30,'bold'),foreground='#1d7150').grid(row=1,column=0,sticky='w')
    s.copy_button=ttk.Button(digits,text='复制',width=4,command=s.manual_copy,state='disabled');s.copy_button.grid(row=1,column=1,padx=(4,0))
    s.result_hint=tk.StringVar(value='连续两次一致后自动复制')
    ttk.Label(digits,textvariable=s.result_hint,wraplength=215,font=('Microsoft YaHei',8)).grid(row=2,column=0,columnspan=2,sticky='ew',pady=(0,3))
    s.history_text=tk.StringVar(value='最近复制：暂无')
    ttk.Label(digits,textvariable=s.history_text,width=28,foreground='#798477',font=('Microsoft YaHei',8)).grid(row=3,column=0,columnspan=2,sticky='w')
    s.history=[]
    actions=ttk.Frame(side);actions.grid(row=1,column=0,sticky='ew',pady=(7,0))
    s.start_button=ttk.Button(actions,text='开始识别',width=7,style='Accent.TButton',command=s.toggle,state='disabled');s.start_button.pack(side='left')
    s.once_button=ttk.Button(actions,text='识别一次',width=7,command=lambda:s.submit(True),state='disabled');s.once_button.pack(side='left',padx=4)
    ttk.Button(actions,text='断开',width=4,command=s.disconnect).pack(side='left')

    paste=ttk.LabelFrame(side,text='游戏输入框点击粘贴',padding=(10,5));paste.grid(row=2,column=0,sticky='ew',pady=(6,0))
    s.input_mode=tk.StringVar(value='自动定位确认弹窗')
    s.input_mode_combo=ttk.Combobox(paste,state='readonly',textvariable=s.input_mode,values=['自动定位确认弹窗','手动标记位置']);s.input_mode_combo.pack(fill='x',pady=(0,4))
    s.input_mode_combo.bind('<<ComboboxSelected>>',lambda event:s.input_mode_changed())
    s.mark_button=ttk.Button(paste,text='手动标记（其他窗口）',command=s.begin_input_mark);s.mark_button.pack(fill='x')
    s.paste_enabled=tk.BooleanVar(value=True)
    ttk.Checkbutton(paste,text='仅输入框内左键点击粘贴',variable=s.paste_enabled,command=s.paste_setting_changed).pack(anchor='w',pady=(6,2))
    s.replace_existing=tk.BooleanVar(value=True)
    ttk.Checkbutton(paste,text='替换已有内容（Ctrl+A）',variable=s.replace_existing).pack(anchor='w')
    s.input_hint=tk.StringVar(value='等待确认弹窗，定位后直接点击游戏输入框。')
    ttk.Label(paste,textvariable=s.input_hint,wraplength=215,foreground='#798477',font=('Microsoft YaHei',8)).pack(anchor='w',pady=(3,2))
    s.paste_status=tk.StringVar(value='等待你点击游戏里的输入框。')
    ttk.Label(paste,textvariable=s.paste_status,wraplength=215,foreground='#9b4b0e',font=('Microsoft YaHei',8)).pack(anchor='w')
    s.settings_button=ttk.Button(side,text='识别设置与游戏配置…',command=s.show_settings)
    s.settings_button.grid(row=3,column=0,sticky='ew',pady=(6,0))
    ttk.Label(side,text='定位不明确时不发送按键',font=('Microsoft YaHei',8),foreground='#798477').grid(row=4,column=0,sticky='w',pady=4)

    s.mode=tk.StringVar(value='确认提示文字');s.length=tk.StringVar(value='4')
    s.interval=tk.StringVar(value='0.8');s.threshold=tk.StringVar(value='75')
    s.keywords=tk.StringVar(value=','.join(KEYWORDS));s.profile=tk.StringVar(value='')
    s.status=tk.StringVar(value='正在启动离线识别引擎…')
    footer=ttk.Frame(s.host,padding=(12,8,12,8));footer.grid(row=2,column=0,sticky='ew')
    status=ttk.Label(footer,textvariable=s.status,wraplength=860,font=('Microsoft YaHei',8));status.pack(anchor='w',fill='x')
    footer.bind('<Configure>',lambda event:status.configure(wraplength=max(280,event.width-24)))

def draw_empty(canvas):
    canvas.delete('all');x=max(1,canvas.winfo_width())/2;y=max(1,canvas.winfo_height())/2
    canvas.create_rectangle(x-40,y-62,x+40,y-14,outline='#aebfb2',width=2)
    canvas.create_line(x-40,y-51,x+40,y-51,fill='#aebfb2')
    canvas.create_text(x,y+8,text='连接游戏后显示实时预览',fill='#547361',font=('Microsoft YaHei',12,'bold'))
    canvas.create_text(x,y+36,text='上方选择游戏窗口 → 连接窗口',fill='#849589',font=('Microsoft YaHei',9))

def open_settings(s):
    if s.settings_dialog is not None and s.settings_dialog.winfo_exists():
        s.settings_dialog.lift();return
    dialog=tk.Toplevel(s.root);s.settings_dialog=dialog
    dialog.title('识别设置与游戏配置');dialog.geometry('450x460');dialog.minsize(400,430);dialog.transient(s.root)
    notebook=ttk.Notebook(dialog);notebook.pack(fill='both',expand=True,padx=10,pady=10)
    recognition=ttk.Frame(notebook,padding=14);profiles=ttk.Frame(notebook,padding=14)
    notebook.add(recognition,text='识别设置');notebook.add(profiles,text='游戏配置')
    recognition.columnconfigure(1,weight=1)
    def field(row,label,widget):
        ttk.Label(recognition,text=label).grid(row=row,column=0,sticky='w',pady=6,padx=(0,12))
        widget.grid(row=row,column=1,sticky='ew',pady=6)
    mode=ttk.Combobox(recognition,textvariable=s.mode,state='readonly',values=['确认提示文字','仅数字区域'])
    mode.bind('<<ComboboxSelected>>',lambda event:s.settings_changed());field(0,'识别方式',mode)
    length=ttk.Combobox(recognition,textvariable=s.length,state='readonly',values=['不限','3','4','5','6','7','8'])
    length.bind('<<ComboboxSelected>>',lambda event:s.settings_changed());field(1,'数字位数',length)
    for row,label,var in [(2,'间隔（秒）',s.interval),(3,'最低置信度 %',s.threshold)]:
        entry=ttk.Entry(recognition,textvariable=var);entry.bind('<FocusOut>',lambda event:s.settings_changed());field(row,label,entry)
    ttk.Label(recognition,text='提示关键词（逗号分隔）').grid(row=4,column=0,columnspan=2,sticky='w',pady=(12,5))
    keywords=ttk.Entry(recognition,textvariable=s.keywords);keywords.grid(row=5,column=0,columnspan=2,sticky='ew')
    keywords.bind('<FocusOut>',lambda event:s.settings_changed())
    ttk.Label(recognition,text='梦幻西游默认识别整窗中的确认数字。\n“仅数字区域”需要先框选只有数字的区域。',wraplength=365,foreground='#798477').grid(row=6,column=0,columnspan=2,sticky='w',pady=14)
    ttk.Button(recognition,text='打开图片识别…',command=s.open_image).grid(row=7,column=0,columnspan=2,sticky='w')
    ttk.Label(profiles,text='游戏配置名称').pack(anchor='w',pady=(0,6))
    s.profile_combo=ttk.Combobox(profiles,textvariable=s.profile,values=list(s.profiles));s.profile_combo.pack(fill='x')
    row=ttk.Frame(profiles);row.pack(fill='x',pady=10)
    ttk.Button(row,text='保存当前配置',command=s.save_profile).pack(side='left')
    ttk.Button(row,text='载入配置',command=s.load_profile).pack(side='left',padx=8)
    ttk.Label(profiles,text='保存识别范围、输入框位置和识别参数。\n窗口尺寸一致时可载入；不同尺寸分别保存。',wraplength=365,foreground='#798477').pack(anchor='w',pady=10)
    ttk.Button(dialog,text='关闭设置',command=dialog.destroy).pack(anchor='e',padx=10,pady=(0,10))
