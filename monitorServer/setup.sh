#!/bin/bash

#set log dir
mkdir -p /var/log/cloudmonitor

#copy the pyc files into sys python path
mkdir -p /usr/lib/python2.7/dist-packages/cloudmonitor
cp ./monitorServer.pyc /usr/lib/python2.7/dist-packages/cloudmonitor/

#copy the config file into sys config path
mkdir -p /etc/cloudmonitor
cp ./monitorServer.conf /etc/cloudmonitor/

#make monitorServer startup 
sed -i '1apython /usr/lib/python2.7/dist-packages/cloudmonitor/monitorServer.pyc' /etc/rc.local
