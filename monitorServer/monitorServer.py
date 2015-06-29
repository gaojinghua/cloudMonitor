'''
Created on 2014-10-25

@author: Jhgao
'''

import zmq
import zmq.backend.cython

import os
import sys
import abc
import time
import logging
import threading
import platform
import traceback
import logging.handlers
from ConfigParser import SafeConfigParser

import socket
import MySQLdb
import pymongo
import string
import datetime

try:
    import simplejson as json
except ImportError:
    import json


LOG = logging.getLogger('myLogger')

class Config(SafeConfigParser):
    defaults = {}

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
    def pull_url(self):
        return self.get('DEFAULT', 'pull_url')

    @property
    def conf_path(self):
        return '/etc/cloudmonitor/monitorServer.conf'

    @property
    def log_path(self):
        return self.get('DEFAULT', 'log_path')
    
    @property
    def admin_host(self):
        return self.get('authtoken', 'admin_host')
    @property
    def admin_tenant_name(self):
        return self.get('authtoken', 'admin_tenant_name')
    
    @property
    def admin_user(self):
        return self.get('authtoken', 'admin_user')
    
    @property
    def admin_password(self):
        return self.get('authtoken', 'admin_password')
    
    @property
    def db_host(self):
        return self.get('mysql', 'db_host')
    
    @property
    def db_user(self):
        return self.get('mysql', 'db_user')
    
    @property
    def db_user_pw(self):
        return self.get('mysql', 'db_user_pw')
    
    @property
    def db_name(self):
        return self.get('mysql', 'db_name')
    
    @property
    def db_port(self):
        return int(self.get('mysql', 'db_port'))
    
    @property
    def db_charset(self):
        return self.get('mysql', 'charset')
    
    @property
    def token_update_time(self):
        return self.get('DEFAULT', 'token_update_time')
    
    @property
    def mongo_backup_time(self):
        return self.get('mongodb','mongo_backup_time')
    
    @property
    def mongo_host(self):
        return self.get('mongodb','mongo_host')
    
    @property
    def mongo_user(self):
        return self.get('mongodb','mongo_user')
    
    @property
    def mongo_user_pw(self):
        return self.get('mongodb','mongo_user_pw')
    
    @property
    def mongo_db_name(self):
        return self.get('mongodb','mongo_db_name')
    
    @property
    def mongo_port(self):
        return self.get('mongodb','mongo_port')
    
    @property
    def mongo_file_path(self):
        return self.get('mongodb','mongo_file_path')
    

    def echo(self):
        with open(self.fp, 'w') as fh:
            self.write(fh)

enConfig = Config()

class GetToken(threading.Thread):
    def __init__(self):
        super(GetToken,self).__init__()
        self.timeSec = string.atof(enConfig.token_update_time)*60
      
    def run(self):
        global token
        while True:
            token = self.getToken()
            time.sleep(self.timeSec)

    def getToken(self):
        command = "curl -X POST -d '{\"auth\":{\"tenantName\":\"" + enConfig.admin_tenant_name+\
        "\",\"passwordCredentials\":{\"username\":\"" + enConfig.admin_user + "\",\"password\":\""\
        + enConfig.admin_password + "\"}}}' -H \"Content-type: application/json\" http://" \
        + enConfig.admin_host + ":35357/v2.0/tokens | python -mjson.tool"

        response = os.popen(command,'r')
        data = json.loads(response.read())
        now_token = data['access']['token']['id']
        LOG.info("token is: %s",now_token)
        return now_token


class SubscribeBase(object):
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def cast(self, Context):
        pass


class ZMQSubscribe(SubscribeBase):
    def __init__(self, addr):
        LOG.info("subscribe from %s",addr)
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PULL)
        self.socket.bind("tcp://%s" % addr)

    def cast(self):
        recv = self.socket.recv_multipart()
        SaveDataToDB(recv)
        

    def close(self):
        self.socket.close()
    

def castServer(addr):
    try:
        pub = ZMQSubscribe(addr)
        pub.cast()
    except:
        import traceback
        LOG.error(traceback.format_exc())



def set_log():
    fm = logging.Formatter('[%(asctime)s] %(levelname)s %(lineno)d %(message)s')
    fh = logging.handlers.TimedRotatingFileHandler(enConfig.log_path, 'H', 24, 7)
    fh.setFormatter(fm)
    sh = logging.StreamHandler()
    sh.setFormatter(fm)
    LOG.addHandler(fh)
    LOG.addHandler(sh)
    LOG.setLevel(logging.INFO)
    LOG.info('Starting logging')


class ServerManager(threading.Thread):
    def __init__(self):
        super(ServerManager, self).__init__()

    def run(self):
        while True:
            self.polling_task()

    def polling_task(self):
        LOG.info('Start polling task with config: %s', enConfig.pull_url)
        castServer(enConfig.pull_url)
        LOG.info('End polling task')


class SetVmData(threading.Thread):
    dataSocket = socket.socket()
    
    def __init__(self):
        super(SetVmData, self).__init__()
	dataURL = enConfig.data_url.split(':')
        host = dataURL[0]
        port = int(dataURL[1])
        self.dataSocket.bind((host,port))
        self.dataSocket.listen(20)
        
    def run(self):
        while True:
            client,addr = self.dataSocket.accept()
            LOG.info('Got Connection from %s',addr)
            try:
                reservation_id = client.recv(1024)
                vmdata = self.getVmDataFromDB(reservation_id)  
                client.send(vmdata)
            except socket.error,e:
                LOG.error('Error sockets files %s',e)
            finally:
                client.close()
            
    def getVmDataFromDB(self,reserv_id):
        try:
            connection = MySQLdb.connect(host=enConfig.db_host,user=enConfig.db_user,
				    passwd=enConfig.db_user_pw,db=enConfig.db_name,
				    port=enConfig.db_port,charset=enConfig.db_charset)
            cursor = connection.cursor()
            sql = "select uuid,user_id,project_id from instances where reservation_id=%s"
            cursor.execute(sql,reserv_id)
            results = cursor.fetchone()
            cursor.close()
        except MySQLdb.Error,e:
            LOG.error("error (%s) occured when using mysql",e)

        if results != None:
            vmData = '['+ results[0].encode('utf-8') +',' + results[1].encode('utf-8') + ',' +\
                    results[2].encode('utf-8') + ']'
        else:
            LOG.error("didnot get any results from mysql DB")
            vmData = '[]'
        
        return vmData



def formCommand(user_id,resource_id,project_id,counter_name,counter_unit,counter_volume,counter_type):
    global token
    return "curl -X POST -H 'X-Auth-Token:" + token + \
            "' -H 'Content-Type: application/json' -d '[{\"counter_name\":\"" + counter_name + \
            "\",\"user_id\":\"" + user_id +  "\",\"resource_id\":\"" + resource_id + \
            "\",\"resource_metedata\":{},\"counter_unit\":\"" + counter_unit + \
	    "\",\"counter_volume\":\"" + counter_volume + "\",\"project_id\":\"" + project_id + \
	    "\",\"counter_type\":\"" + counter_type + "\"}]' http://" + enConfig.admin_host + \
	    ":8777/v2/meters/" + counter_name


def SaveDataToDB(recv):
    resource_id = recv[0]
    user_id = recv[1]
    project_id = recv[2]
    data = json.loads(recv[3])

    net_bytes_sent = data['network']['net_io_counters']['bytes_sent']
    net_bytes_recv = data['network']['net_io_counters']['bytes_recv']
    net_sent_bps = data['network']['net_io_counters']['network_sent_bps']
    net_recv_bps = data['network']['net_io_counters']['network_recv_bps']
    net_error_out = data['network']['net_io_counters']['network_error_out']
    net_error_in = data['network']['net_io_counters']['network_error_in']
    
    mem_total = data['memory']['memory']['total']
    mem_util = data['memory']['memory']['percent']

    disk_total_write_bytes = data['disk']['disk_io_counters']['write_bytes']
    disk_total_read_bytes = data['disk']['disk_io_counters']['read_bytes']
    disk_write_bps = data['disk']['disk_io_counters']['disk_write_Bps']
    disk_read_bps = data['disk']['disk_io_counters']['disk_read_Bps']

    cpu_percent = data['cpu']['cpu_percent']

    command = {}
    command[0] = formCommand(user_id,resource_id,project_id,"net_bytes_sent","B",str(net_bytes_sent),"cumulative")
    command[1] = formCommand(user_id,resource_id,project_id,"net_bytes_recv","B",str(net_bytes_recv),"cumulative")
    command[2] = formCommand(user_id,resource_id,project_id,"net_sent_bps","B/s",str(net_sent_bps),"gauge")
    command[3] = formCommand(user_id,resource_id,project_id,"net_recv_bps","B/s",str(net_recv_bps),"gauge")
    command[4] = formCommand(user_id,resource_id,project_id,"net_error_out","B",str(net_error_out),"cumulative")
    command[5] = formCommand(user_id,resource_id,project_id,"net_error_in","B",str(net_error_in),"cumulative")
    command[6] = formCommand(user_id,resource_id,project_id,"mem_total","MB",str(mem_total),"gauge")
    command[7] = formCommand(user_id,resource_id,project_id,"mem_util","%",str(mem_util),"gauge")
    command[8] = formCommand(user_id,resource_id,project_id,"disk_total_write_bytes","B",str(disk_total_write_bytes),"cumulative")
    command[9] = formCommand(user_id,resource_id,project_id,"disk_total_read_bytes","B",str(disk_total_read_bytes),"cumulative")
    command[10] = formCommand(user_id,resource_id,project_id,"disk_write_bps","B/s",str(disk_write_bps),"gauge")
    command[11] = formCommand(user_id,resource_id,project_id,"disk_read_bps","B/s",str(disk_read_bps),"gauge")
    command[12] = formCommand(user_id,resource_id,project_id,"cpu_percent","%",str(cpu_percent),"gauge")
  
    i = 12
    while(i>=0):
        response = os.popen(command[i],'r')
        LOG.info("storing to db,the results is %s",response.read())
        i = i - 1
        

class BackDB(threading.Thread):
    def __init__(self):
        super(BackDB, self).__init__()

    def run(self):
        while True:
            time.sleep(string.atof(enConfig.mongo_backup_time)*3600)
            now_time = datetime.datetime.now()
            print enConfig.mongo_backup_time
            select_time = now_time - datetime.timedelta(hours = string.atof(enConfig.mongo_backup_time))
            self.deleteDB(select_time)
            self.backDB()
                     
    
    def backDB(self):
        if enConfig.mongo_file_path.endswith('/'):
            file_path = enConfig.mongo_file_path + str(time.time())
        else:
            file_path = enConfig.mongo_file_path + '/' + str(time.time())

        back_cmd = "mongodump -u "+ enConfig.mongo_user + \
                " -p " + enConfig.mongo_user_pw + " -h " + enConfig.mongo_host +\
                " -d  " + enConfig.mongo_db_name + " -c meter " + " -o " +\
                file_path + " --port " + enConfig.mongo_port
        
        print back_cmd
        response = os.popen(back_cmd,'r')
        print response.read()
     
    
    def deleteDB(self,select_time):
        conn = pymongo.Connection(enConfig.mongo_host,string.atoi(enConfig.mongo_port))
        db = conn.ceilometer
        db.authenticate(enConfig.mongo_user,enConfig.mongo_user_pw)
        db.meter.remove({"timestamp":{"$lt":select_time}})
        print 'yes'
                                

if __name__=='__main__':
    try:
        set_log()
    except:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == 'echo':
        config = Config()
        config.echo()
        sys.exit()

    token = GetToken().getToken()
    GetToken().start()
    SetVmData().start()
    ServerManager().start()
    BackDB().start()
