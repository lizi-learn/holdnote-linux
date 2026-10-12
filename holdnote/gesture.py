"""Pure gesture state. No captured text, key logs or desktop dependency."""
from dataclasses import dataclass


@dataclass
class Outcome:
    action:str
    clicked:bool=False


class Gesture:
    def __init__(self,hold_ms=450):self.threshold=hold_ms/1000;self.reset()
    def reset(self):self.down=False;self.armed=False;self.blocked=False;self.clicked=False;self.started=0.0
    def press(self,now):
        if self.down:return Outcome('ignore')
        self.reset();self.down=True;self.started=now;return Outcome('pending')
    def key(self):
        if self.down:self.blocked=True;return Outcome('cancel')
        return Outcome('ignore')
    def button(self,button):
        if self.down and not self.blocked and button==1:self.clicked=True
    def tick(self,now):
        if self.down and not self.blocked and not self.armed and now-self.started>=self.threshold:
            self.armed=True;return Outcome('arm')
        return Outcome('ignore')
    def release(self,now):
        if not self.down:return Outcome('ignore')
        if self.blocked:out=Outcome('cancel')
        elif now-self.started<self.threshold:out=Outcome('overview')
        else:out=Outcome('capture',self.clicked)
        self.reset();return out
