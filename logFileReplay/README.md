## Use this script to deploy LogFileReplay Agent on multiple Hosts
# InsightAgent: LogFileReplay
Agent Type: LogFileReplay
Platform: Linux

InsightAgent supports replay mode of json log files in which the data from the json file is read and sent to insightfinder server. A sample log file is as follows:

```json
[{"eventId": 1480750759682, "data": " INFO org.apache.hadoop.hdfs.server.namenode.TransferFsImage: Downloaded file fsimage.ckpt_0000000000000000020 size 120 bytes.\n", "tag": "hadoop"}, {"eventId": 1480750759725, "data": " INFO org.apache.hadoop.hdfs.server.namenode.NNStorageRetentionManager: Going to retain 2 images with txid >= 18\n", "tag": "hadoop"}, {"eventId": 1480754359850, "data": " INFO org.apache.hadoop.hdfs.server.namenode.FSNamesystem: Roll Edit Log from 127.0.0.1\n", "tag": "hadoop"}]
```

##### Instructions to register a project in Insightfinder.com
- Go to the link https://insightfinder.com/
- Sign in with the user credentials or sign up for a new account.
- Go to Settings and Register for a project under "Insight Agent" tab.
- Give a project name, select Project Type as "Log" with a type of "File Replay".
- Note down the project name and license key which will be used for agent installation. The license key is also available in "User Account Information". To go to "User Account Information", click the userid on the top right corner.

### Prerequisites:
Python 2.7
SSH accesses to all hosts where agents will be installed are required. SSH key can be generated by following this link:
https://www.ssh.com/ssh/keygen/

Tested with ansible version 2.2.3.0

### Install wget to download the required files:
#### For Debian and Ubuntu
```
sudo -E apt-get update
sudo -E apt-get install wget
```
#### For Fedora and RHEL-derivatives
```
sudo -E yum update
sudo -E yum install wget
```
Note: If you are using proxy, the proxy needs to be set for both the current user and root.

#### Installation:
1) Use the following command to download the insightfinder agent code. You can skip this step if you have the offline installer package.
```
wget --no-check-certificate https://github.com/insightfinder/InsightAgent/archive/master.tar.gz -O insightagent.tar.gz
or
wget --no-check-certificate http://github.com/insightfinder/InsightAgent/archive/master.tar.gz -O insightagent.tar.gz
```

Untar using this command.
```
tar -xvf insightagent.tar.gz
cd InsightAgent-master/
```

If you do not need to distribute the replay script, you can skip to **Sending Data** below.

2) Download the agent Code which will be distributed to other machines(not required if you have the offline installation package)
```
cd deployment/DeployAgent/files/
sudo -E ./downloadAgentSSL.sh
# or
sudo -E ./downloadAgentNoSSL.sh
```

3) Install Ansible, if this the first agent you are installing from this machine.
```
cd ..
sudo -E ./installAnsible.sh
```

4) Open and modify the inventory file
```
# vi inventory
[nodes]
HOST ansible_user=USER ansible_ssh_private_key_file=SOMETHING
###We can specify the host name with ssh details like this for each host
##If you have the ssh key
#192.168.33.10 ansible_user=vagrant ansible_ssh_private_key_file=/home/private_key

##If you have the password
#192.168.33.20 ansible_user=vagrant ansible_ssh_pass=ssh_password


##We can also specify the host names here and the ssh details under [nodes:vars] if they have have the same ssh credentials
##(Only one of ansible_ssh_pass OR ansible_ssh_private_key_file is required)
#192.168.33.10
#192.168.33.15

[nodes:vars]
#ansible_user=vagrant
#ansible_ssh_pass=ssh_password
#ansible_ssh_private_key_file=/home/private_key

[all:vars]
##install or uninstall
ifAction=install

##Login User In Insightfinder Application
ifUserName=

##Project Name In Insightfinder Application
ifProjectName=

##User's License Key in Application
ifLicenseKey=

##Sampling interval could be an integer indicating the number of minutes or "10s" indicating 10 seconds.
ifSamplingInterval=1

##Agent type
#
ifAgent=LogFileReplay
#

##The server reporting Url(Do not change unless you have on-prem deployment)
ifReportingUrl=https://app.insightfinder.com
```

5) Run the playbook
```
ansible-playbook insightagent.yaml
```

### Sending Data
If you skipped to this step, set up `common/config.ini` in the InsightAgent folder. Note that the `[metrics]` section can be left blank when replaying logs.

Run the following command for each log json file. You should be inside InsightAgent-master directory while running the command.
```
cd InsightAgent-master/
python common/reportMetrics.py -w https://app.insightfinder.com -m logFileReplay -f PATH/TO/JSON_FILE
```
Note: If replaying to an on-prem installation, add the server ip and port after the -w option.

If you want to send a list of logs within a directory, you can use:
```
find /PATH/TO/DIRECTORY -maxdepth 1 -type f -exec python common/reportMetrics.py... -f {} \;
```

If your data is not pre-formatted JSON, we support the following log types:
* gpfs
* db2
* network-logs

You can specify that your file is one of the above types by passing it as an argument for the -t flag
```
python common/reportMetrics.py -w https://app.insightfinder.com -m logFileReplay -t gpfs -f PATH/TO/GPFS_FILE

python common/reportMetrics.py -w https://app.insightfinder.com -m logFileReplay -t db2 -f PATH/TO/DB2_FILE

python common/reportMetrics.py -w https://app.insightfinder.com -m logFileReplay -t network-log -f PATH/TO/NETWORK-LOG_FILE
```

### Uninstallation:
Note: Uninstallation is required before you can install any other Metric agent(e.g. cgroup) or you want to reinstall the current collectd agent.

1) Open and modify the inventory file
```
[all:vars]
##install or uninstall
ifAction=uninstall
```

2) Run the playbook
```
ansible-playbook insightagent.yaml
```
