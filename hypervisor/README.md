# InsightAgent: hypervisor
Agent Type: hypervisor

Platform: VMkernel

InsightFinder agent can be used to monitor system performance metrics of hypervisor hosts.

##### Instructions to register a project in Insightfinder.com
- Go to the link https://insightfinder.com/
- Sign in with the user credentials or sign up for a new account.
- Go to Settings and Register for a project under "Insight Agent" option.
- Give a project name, select Project Type as "Private Cloud".
- Note down license key which is available in "User Account Information". To go to "User Account Information", click the userid on the top right corner. Enter those details in the form.

##### Pre-requisites:
SSH accesses to all hosts where agents will be installed are required. SSH key can be generated by following this link:
https://www.ssh.com/ssh/keygen/

Ansible is required to use the provided installer. Tested with ansible version 2.4.0.0

#### Get copy of the deployment script:
1) Use the following command to download the insightfinder agent code.
```
wget --no-check-certificate https://github.com/insightfinder/InsightAgent/archive/master.tar.gz -O insightagent.tar.gz
or
wget --no-check-certificate http://github.com/insightfinder/InsightAgent/archive/master.tar.gz -O insightagent.tar.gz

```
Untar using this command.
```
tar -xvf insightagent.tar.gz
```
```
cd InsightAgent-master/deployment/DeployAgent/
sudo -E ./installAnsible.sh
```
2) Open and modify the inventory file

```
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
ifAgent=hypervisor

##The server reporting Url(Do not change unless you have on-prem deployment)
ifReportingUrl=https://app.insightfinder.com
```


3) Download the agent Code which will be distributed to other machines
```
cd files
sudo -E ./downloadAgentZip.sh
```
4) Run the playbook(Go back to the DeployAgent directory)
```
cd ..
ansible-playbook -i inventory insightagent.yaml
```

### Uninstallation:
Note: Uninstallation is required before you can install any other Metric agent(e.g. cgroup) or you want to reinstall the current collectd agent.

1) Open and modify the inventory file
```
[all:vars]
##install or uninstall
ifAction=uninstall
```

```
##Agent type
ifAgent=hypervisor
```
2) Run the playbook
```
ansible-playbook -i inventory insightagent.yaml
```
