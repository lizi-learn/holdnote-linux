"""GNOME/X11 glue. Listen to gestures only, never persist ordinary key events."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

import gi
gi.require_version('Gtk','3.0')
gi.require_version('Gdk','3.0')
from gi.repository import Gdk, Gio, GLib, Gtk
from Xlib import X, XK, display
from Xlib.ext import record
from Xlib.protocol import rq

from .config import CONFIG_DIR, DATA_DIR, load, private_write, secret
from .client import Memos
from .gesture import Gesture
from .outbox import Outbox, Worker

SETTING_STATE=CONFIG_DIR/'desktop-state.json'


def restore_settings():
    if not SETTING_STATE.exists():return
    saved=json.loads(SETTING_STATE.read_text())
    for item in saved:
        settings=Gio.Settings.new(item['schema'])
        # Don't overwrite a manual desktop change made while HoldNote ran.
        if settings.get_string(item['key'])==item['applied']:settings.set_string(item['key'],item['original'])
    Gio.Settings.sync();SETTING_STATE.unlink()


def apply_settings():
    restore_settings()
    saved=[]
    for schema,key,value in [('org.gnome.mutter','overlay-key',''),('org.gnome.desktop.wm.preferences','mouse-button-modifier','<Alt>')]:
        settings=Gio.Settings.new(schema)
        saved.append({'schema':schema,'key':key,'original':settings.get_string(key),'applied':value})
    private_write(SETTING_STATE,json.dumps(saved))
    try:
        for item in saved:
            if not Gio.Settings.new(item['schema']).set_string(item['key'],item['applied']):raise RuntimeError('GNOME setting could not be changed')
        Gio.Settings.sync()
    except Exception:restore_settings();raise


def notify(kind,value=None):
    messages={
        'saved':('已保存到 Memos','文字已私密保存。'),
        'queued':('已进入加密待发队列','网络暂不可用，恢复后自动重试。'),
        'failed':('文字暂未发送','内容仍在加密队列。请运行 holdnote doctor / holdnote retry。'),
        'empty':('没有保存内容','请先长按 Super，再用鼠标拖选文字，最后松开。'),
        'unchanged':('没有新的文字选择','未发送旧的 PRIMARY 内容。请等提示出现后再拖选。'),
        'too-long':('选中文字过长','请缩小选区再保存。'),
        'ready':('HoldNote 已启动','长按 Super → 拖选文字 → 松开保存；Esc 取消。'),
        'stopped':('HoldNote 已停止','原桌面快捷键设置已恢复。'),
    }
    title,body=messages[kind]
    if kind=='saved' and isinstance(value,int):body='已私密保存 '+str(value)+' 字。'
    subprocess.Popen(['notify-send','--app-name=HoldNote','--icon=document-save','--expire-time=3500',title,body],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def overview():
    try:
        bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
        result=bus.call_sync('org.gnome.Shell','/org/gnome/Shell','org.freedesktop.DBus.Properties','Get',GLib.Variant('(ss)',('org.gnome.Shell','OverviewActive')),GLib.VariantType('(v)'),Gio.DBusCallFlags.NONE,1000,None)
        active=result.get_child_value(0).get_variant().get_boolean()
        bus.call_sync('org.gnome.Shell','/org/gnome/Shell','org.freedesktop.DBus.Properties','Set',GLib.Variant('(ssv)',('org.gnome.Shell','OverviewActive',GLib.Variant('b',not active))),None,Gio.DBusCallFlags.NONE,1000,None)
    except Exception:
        # Fall back to the standard GNOME overview shortcut, without text input.
        subprocess.Popen(['gdbus','call','--session','--dest','org.gnome.Shell','--object-path','/org/gnome/Shell','--method','org.gnome.Shell.ShowApplications'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


class Hint:
    def __init__(self):
        self.window=Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_decorated(False);self.window.set_accept_focus(False);self.window.set_focus_on_map(False)
        self.window.set_skip_taskbar_hint(True);self.window.set_skip_pager_hint(True);self.window.set_keep_above(True)
        self.window.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.window.set_resizable(False)
        label=Gtk.Label(label='HoldNote  ·  拖选文字，松开 Super 保存  ·  Esc 取消')
        label.set_margin_top(12);label.set_margin_bottom(12);label.set_margin_start(18);label.set_margin_end(18)
        self.window.add(label)
        css=Gtk.CssProvider();css.load_from_data(b'window {background:#173d35;color:#fff;border-radius:12px;} label {font-size:14px;}')
        self.window.get_style_context().add_provider(css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    def show(self):
        screen=Gdk.Screen.get_default();monitor=Gdk.Display.get_default().get_primary_monitor()
        area=monitor.get_workarea() if monitor else Gdk.Rectangle()
        width=self.window.get_preferred_width()[1]
        self.window.move(area.x+max(0,(area.width-width)//2),area.y+18)
        self.window.show_all()
    def hide(self):self.window.hide()


class App:
    def __init__(self):
        if os.environ.get('XDG_SESSION_TYPE')!='x11':raise RuntimeError('v0.1 requires a GNOME/X11 session; Wayland is not supported yet')
        if 'GNOME' not in os.environ.get('XDG_CURRENT_DESKTOP','').upper():raise RuntimeError('v0.1 supports GNOME only; refusing to change another desktop\'s shortcuts')
        self.config=load();self.gesture=Gesture(self.config['hold_ms']);self.loop=GLib.MainLoop()
        self.control=display.Display();self.events=display.Display()
        if not self.events.has_extension('RECORD'):raise RuntimeError('X11 RECORD is unavailable')
        self.supers={self.control.keysym_to_keycode(XK.string_to_keysym(k)) for k in ['Super_L','Super_R']}
        self.shifts={self.control.keysym_to_keycode(XK.string_to_keysym(k)) for k in ['Shift_L','Shift_R']}
        self.pressed_super=None;self.running=True;self.capture_generation=0;self.baseline=None;self.raw_super_down=False;self.hold_timer=None
        self.primary=Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY);self.hint=Hint()
        self.outbox=Outbox(DATA_DIR/'outbox.db',secret(self.config,'outbox-key'))
        self.worker=Worker(self.outbox,Memos(self.config['memos_url'],secret(self.config,'memos-token')),notify)
        self.context=self.events.record_create_context(0,[record.AllClients],[{'core_requests':(0,0),'core_replies':(0,0),'ext_requests':(0,0,0,0),'ext_replies':(0,0,0,0),'delivered_events':(0,0),'device_events':(X.KeyPress,X.ButtonRelease),'errors':(0,0),'client_started':False,'client_died':False}])
    def baseline_ready(self,clipboard,text,generation):
        if generation==self.capture_generation:self.baseline=text or ''
    def arm(self):
        self.baseline=None
        self.primary.request_text(self.baseline_ready,self.capture_generation)
        self.hint.show()
    def tick(self):
        self.hold_timer=None
        if self.gesture.tick(time.monotonic()).action=='arm':self.arm()
        return False
    def cancel_timer(self):
        if self.hold_timer is not None:GLib.source_remove(self.hold_timer);self.hold_timer=None
    def event(self,kind,detail):
        if not self.running:return False
        now=time.monotonic()
        if kind==X.KeyPress:
            if detail in self.supers:
                if self.pressed_super is not None and self.pressed_super!=detail:self.gesture.key();self.hint.hide()
                else:
                    self.pressed_super=detail
                    if self.gesture.press(now).action=='pending':
                        self.capture_generation+=1;self.baseline=None
                        self.hold_timer=GLib.timeout_add(self.config['hold_ms']+5,self.tick)
            elif detail not in self.shifts:
                if self.gesture.key().action=='cancel':self.hint.hide();self.cancel_timer()
        elif kind==X.ButtonPress:self.gesture.button(detail)
        elif kind==X.KeyRelease and detail==self.pressed_super:
            result=self.gesture.release(now);self.pressed_super=None;self.hint.hide();self.cancel_timer()
            if result.action=='overview':overview()
            elif result.action=='capture':
                if not result.clicked or self.baseline is None:notify('empty')
                else:self.primary.request_text(self.selection_ready,(self.baseline,self.capture_generation))
        return False
    def selection_ready(self,clipboard,text,context):
        baseline,generation=context
        if generation!=self.capture_generation:return
        text=text or ''
        if not text.strip():return notify('empty')
        if text==baseline:return notify('unchanged')
        if len(text)>self.config['max_chars']:return notify('too-long')
        tags=' '.join('#'+t for t in self.config.get('tags',[]))
        self.outbox.add(text+('\n\n'+tags if tags else ''));self.worker.wake.set()
    def record_event(self,reply):
        if reply.category!=record.FromServer or reply.client_swapped or not reply.data or reply.data[0]<2:return
        data=reply.data
        while data:
            event,data=rq.EventField(None).parse_binary_value(data,self.events.display,None,None)
            # Unrelated typing is discarded immediately, never decoded/logged.
            if event.type in [X.KeyPress,X.KeyRelease] and event.detail in self.supers:
                GLib.idle_add(self.event,event.type,event.detail)
                self.raw_super_down=event.type==X.KeyPress
            elif self.raw_super_down and event.type in [X.KeyPress,X.ButtonPress]:
                GLib.idle_add(self.event,event.type,event.detail)
    def listen(self):
        try:self.events.record_enable_context(self.context,self.record_event)
        except Exception:
            if self.running:GLib.idle_add(self.shutdown)
    def shutdown(self,*_):
        if not self.running:return False
        self.running=False;self.worker.stop.set();self.worker.wake.set();self.hint.hide();self.cancel_timer()
        try:self.control.record_disable_context(self.context);self.control.flush()
        except Exception:pass
        restore_settings();self.loop.quit();return False
    def run(self):
        # Lock the singleton before touching any desktop settings.
        lock=DATA_DIR/'daemon.lock';fd=os.open(lock,os.O_RDWR|os.O_CREAT,0o600)
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:os.close(fd);raise RuntimeError('HoldNote is already running')
        try:
            apply_settings()
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT,signal.SIGTERM,self.shutdown)
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT,signal.SIGINT,self.shutdown)
            threading.Thread(target=self.worker.run,daemon=True,name='holdnote-sender').start()
            threading.Thread(target=self.listen,daemon=True,name='holdnote-gesture').start()
            notify('ready');self.loop.run()
        finally:
            self.running=False;restore_settings();self.hint.window.destroy();os.close(fd)


def capture_selection(kind='primary'):
    selection=Gdk.SELECTION_PRIMARY if kind=='primary' else Gdk.SELECTION_CLIPBOARD
    return Gtk.Clipboard.get(selection).wait_for_text() or ''
