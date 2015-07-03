'''
Created on 2014-10-25

@author: Jhgao
'''

import zmq
import psutil

import os
import sys
import abc
import copy
import uuid
import time
import logging
import hashlib
import platform
import threading
import traceback
import logging.handlers
from ConfigParser import SafeConfigParser
import socket

try:
    import simplejson as json
except ImportError:
    import json
    
try:
    import wmi
except ImportError:
    pass

HostName = platform.node()
Platform = platform.platform()
HostId = hashlib.md5(str(uuid.getnode())).hexdigest()
CPU = psutil.cpu_count()
RAM = psutil.virtual_memory().total
BASE_NAME = os.path.basename(os.path.abspath('.')).split('.')[0]
LOG = logging.getLogger(BASE_NAME)

realtime_io = {
                "disk_io": (None, None),
                "network_io": (None, None),
}


class Config(SafeConfigParser):
    defaults = {
                'data_url': '192.168.1.214:3344',
                'push_url': '192.168.1.214:1314'}

    def __init__(self, fp=None, *args, **kwargs):
        self.defaults.update(kwargs)
        SafeConfigParser.__init__(self, self.defaults)
        self.pf = platform.platform()
        self.fp = fp or self.conf_path
        self.read(self.fp)
        
    @property
    def data_url(self):
        return self.get('DEFAULT', 'data_url')

    @property
    def push_url(self):
        return self.get('DEFAULT', 'push_url')

    @property
    def conf_path(self):
        if self.pf.startswith('Windows'):
            return os.path.join(os.path.abspath('.'), 'monitorAgent.ini')
        if self.pf.startswith('Linux'):
            return '/etc/cloudmonitor/monitorAgent.ini'

    @property
    def log_path(self):
        if self.pf.startswith('Windows'):
            return os.path.join(os.path.abspath('.'), 'monitorAgent.log')
        if self.pf.startswith('Linux'):
            return '/var/log/cloudmonitor/monitorAgent.log'
        
    @property
    def polling_time(self):
        return self.get("DEFAULT", "polling_time")
    
    def echo(self):
        with open(self.fp, 'w') as fh:
            self.write(fh)


enConfig = Config()

def set_log():
    format = logging.Formatter('[%(asctime)s] %(levelname)s %(lineno)d %(message)s')
    fh = logging.handlers.TimedRotatingFileHandler(enConfig.log_path, 'H', 24, 7)
    fh.setFormatter(format)
    sh = logging.StreamHandler()
    sh.setFormatter(format)
    LOG.addHandler(fh)
    LOG.addHandler(sh)
    LOG.setLevel(logging.INFO)
    LOG.debug('Start %s LOG', BASE_NAME)
    

class IOFetcher(threading.Thread):
    def __init__(self):
        super(IOFetcher, self).__init__()

    def run(self):
        global realtime_io
        while True:
            try:
                data_fir = self.fetch_data()
                time.sleep(1)
                data_sec = self.fetch_data()
                realtime_io = {
                        "disk_io": self.disk_io(data_fir.get("disk"), data_sec.get("disk")),
                        "network_io": self.network_io(data_fir.get("network"), data_sec.get("network"))
                }
            except:
                LOG.error(traceback.format_exc())

    def fetch_data(self):
        return {
                "disk": {
                            "summary": self.disk_io_counters(),
                            "detail": self.disk_io_counters(perdisk=True)
                },
                "network": {
                            "summary": self.net_io_counters(),
                            "detail": self.net_io_counters(pernic=True)
                }
        }

    def disk_io(self, data_fir, data_sec):
        keys = [
                ("disk_read_Bps", "read_bytes"),
                ("disk_write_Bps", "write_bytes"),
                ("disk_read_iops", "read_count"),
                ("disk_write_iops", "write_count")
        ]
        return self.compute(keys, data_fir, data_sec)
        
    def network_io(self, data_fir, data_sec):
        keys = [
                ("network_recv_bps", "bits_recv"),
                ("network_sent_bps", "bits_sent"),
                ("network_recv_pps", "packets_recv"),
                ("network_sent_pps", "packets_sent"),
                ("network_error_in", "errin"),
                ("network_error_out", "errout")
        ]
        return self.compute(keys, data_fir, data_sec)

    def compute(self, keys, data_fir, data_sec):
        res_summary = dict()
        [res_summary.setdefault(k[0], data_sec.get("summary").get(k[1])-data_fir.get("summary").get(k[1])) for k in keys]
        data_sec.get("summary").update(res_summary)
        summary = data_sec.get("summary")
        
        detail = dict()
        for card in data_sec.get("detail").keys():
            fir = data_fir.get("detail").get(card)
            sec = data_sec.get("detail").get(card)
            res_detail = dict()
            [res_detail.setdefault(k[0], sec.get(k[1])-fir.get(k[1])) for k in keys]
            sec.update(res_detail)
            detail.setdefault(card, sec)
        
        return summary, detail

    def net_io_counters(self, pernic=False):
        data = psutil.network_io_counters(pernic=pernic)
        if not pernic:
            res = dict(data._asdict())
            res['bits_recv'] = res.get('bytes_recv')*8
            res['bits_sent'] = res.get('bytes_sent')*8
        else:
            res = {}
            for card, detail in dict(data).items():
                card = card.replace('.', '-')
                res.setdefault(card, detail._asdict())
                res[card]['bits_recv'] = res[card]['bytes_recv']*8
                res[card]['bits_sent'] = res[card]['bytes_sent']*8
        return res

    def disk_io_counters(self, perdisk=False):
        data = psutil.disk_io_counters(perdisk=perdisk)
        if not perdisk:
            res = dict(data._asdict())
        else:
            res = {}
            for disk, detail in dict(data).items():
                res.setdefault(disk, detail._asdict())
        return res
    

class PublishContext(object):
    def __init__(self,**kwargs):
        self.values = kwargs

    def to_dict(self):
        return copy.deepcopy(self.values)

    @classmethod
    def from_dict(cls, values):
        return cls(**values)

    @classmethod
    def marshal(self, ctx):
        ctx_data = ctx.to_dict()
        try:
            return json.dumps(ctx_data)
        except TypeError:
            LOG.error("JSON serialization failed.")

    @classmethod
    def unmarshal(self, data):
        return json.loads(data)


class PTContext(PublishContext):

    def __init__(self,**kwargs):
        super(PTContext, self).__init__(**kwargs)
        self._fields = ['cpu', 'memory', 'disk', 'network', 'process', 'system']


    @property
    def agent_timestamp(self):
        return time.time()

    @property
    def cpu(self):
        return {
                "cpu_percent": psutil.cpu_percent(0.5),
                "cpu_times": psutil.cpu_times_percent(0.5)._asdict()
        }

    @property
    def memory(self):
        return {'memory': dict(psutil.virtual_memory()._asdict()),
                'swap_memory': dict(psutil.swap_memory()._asdict())}

    @property
    def disk(self):
        dpart = [dict(i._asdict()) for i in psutil.disk_partitions()]
        global realtime_io
        summary, detail = realtime_io.get("disk_io")
        for i in dpart:
            if i['fstype']:
                i['disk_usage'] = psutil.disk_usage(i['mountpoint'])

        return {'disk_partitions': dpart,
                'disk': detail,
                'disk_io_counters': summary}

    @property
    def network(self):
        global realtime_io
        summary, detail = realtime_io.get("network_io")
        return {'nic': detail,
                'net_io_counters': summary}

    @property
    def process(self):
        proc = {}
        for p in psutil.process_iter():
            try:
                name = p.name().replace('.', '_')
                if name in proc:
                    memory = dict(p.memory_info()._asdict())
                    proc[name]['memory']['vms'] += memory['vms']
                    proc[name]['memory']['rss'] += memory['rss']
                    proc[name]['cpu'] += p.cpu_percent()
                    proc[name]['process'] += 1
                else:
                    proc[name] = {
                        'memory': dict(p.memory_info()._asdict()),
                        'cpu': p.cpu_percent(),
                        'process': 1,
                        'username': p.username()
                    }
            except Exception:
                continue
            return proc

    @property
    def system(self):
        try:
            cpuinfo = self.cpu_info()
        except:
            cpuinfo = ""
        return {'HostName': HostName,
                'HostId': HostId,
                'boot_time': psutil.boot_time(),
                'Platform': Platform,
                'CPU': str(CPU) + ",[%s]"%self.cpu_info(),
                'RAM': RAM}

    def to_dict(self):
        data = dict((i, getattr(self, i, {})) for i in self._fields)
#        data.update(self.values)
        return data

    def cpu_info(self):
        res = ""
        if "linux" in sys.platform:
            if os.path.exists('/proc/cpuinfo'):
                with open('/proc/cpuinfo') as f:
                    for l in file("/proc/cpuinfo",'r').readlines():
                        if "model name" in l:
                            res = l.split(':')[1].strip()
                            break
        elif "win" in sys.platform:
            import pythoncom
            pythoncom.CoInitialize()
            try:
                c = wmi.WMI()
                cpus = [processor.Name.strip() for processor in c.Win32_Processor()]
                if cpus:
                    res = cpus[0]
            except Exception, err:
                LOG.error(err)
            finally:
                pythoncom.CoUninitialize()
        return res


class PublishBase(object):
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def cast(self, Context):
        pass


class ZMQPublish(PublishBase):
    def __init__(self, addr):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUSH)
        self.socket.connect("tcp://%s" % addr)

    def cast(self, ctx): 
        self.socket.send_multipart(map(bytes, (VMdata[0],VMdata[1],VMdata[2], PublishContext.marshal(ctx))))

    def close(self):
        self.socket.close()


def cast(addr, context):
    try:
        pub = ZMQPublish(addr)
        pub.cast(context)
    except:
        import traceback
        LOG.error(traceback.format_exc())


sendTimes = 1

class AgentManager(threading.Thread):
    def __init__(self):
        super(AgentManager, self).__init__()
        self.polling_time = int(enConfig.polling_time)

    def run(self):
        while True:
            time.sleep(self.polling_time)
            self.polling_task()         

    def polling_task(self):
        LOG.debug('Start polling task with config: %s', enConfig.push_url)
        cast(enConfig.push_url, PTContext())
        LOG.debug('End polling task')


class GetVmData():
    
    def getReservationId(self):
        if Platform.startswith('Windows'):
            pos = ".\curl\curl.exe"
        elif Platform.startswith('Linux'):
            pos = 'curl'
        else:
            LOG.error("the curl command cann't get response")
        request = pos + " http://169.254.169.254/latest/meta-data/reservation-id"
        response=os.popen(request,'r')
        reservation_id = response.read()
        LOG.info('Get reservation_id: %s ', reservation_id)
        return reservation_id

    def getData(self):
        Datasocket = socket.socket()
        dataURL = enConfig.data_url.split(':')
        host = dataURL[0]
        port = int(dataURL[1])
        global VMdata
        try:
            Datasocket.connect((host,port))
            Datasocket.sendall(self.getReservationId())
            recvdata = Datasocket.recv(1024)
            LOG.info("Get infomation from openstack : %s",recvdata)
        except socket.error,e:
            LOG.error('Error sending ReservationData %s', e)
            recvdata = '[]'
        VMdata = recvdata.strip('[]').split(',')
        return recvdata 
        

if __name__=='__main__':
    try:
        set_log()
    except:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == 'echo':
        config = Config()
        config.echo()
        sys.exit()
    # getVMdata first then send this data again to DB
    if (GetVmData().getData() != '[]'):
        AgentManager().start()
        IOFetcher().start()
