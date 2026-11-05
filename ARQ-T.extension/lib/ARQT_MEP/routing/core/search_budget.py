"""Single deadline with observed-yield allocation and stable attempt identities."""
import time
from .candidate_repository import signature


def attempt_seed(global_seed,route_id,source_id,corridor,attempt):
    return int(signature([global_seed,route_id,source_id,corridor,attempt])[:16],16)%(2**32)


class SearchBudgetManager:
    def __init__(self,deadline,cancelled=None,state=None,clock=None):
        self.clock=clock or time.monotonic
        self.deadline=deadline;self.cancelled=cancelled;self.records=(state or {}).get('records',{});self.attempts=(state or {}).get('attempts',{})
        self.reserve=max(.2,min(5,max(0,deadline-self.clock())*.05))

    def stopped(self):return self.clock()>=self.deadline or bool(self.cancelled and self.cancelled())

    def allocate(self,key,pending=1,consolidation=False,deadline=None):
        now=self.clock();limit=min(self.deadline,deadline if deadline is not None else self.deadline)
        remaining=max(0,limit-now-(0 if consolidation else self.reserve))
        record=self.records.get(key,{})
        efficiency=(1+record.get('useful',0))/(1+record.get('failures',0))
        return min(limit,now+remaining/max(1,pending)*min(2,max(.25,efficiency)))

    def next_attempt(self,key):
        count=self.attempts.get(key,0);self.attempts[key]=count+1;return count

    def record(self,key,seconds,useful,reason=None):
        record=self.records.setdefault(key,{'seconds':0,'useful':0,'failures':0})
        record['seconds']+=seconds;record['useful' if useful else 'failures']+=1;record['last_reason']=reason

    def export_state(self):return {'records':self.records,'attempts':self.attempts}
