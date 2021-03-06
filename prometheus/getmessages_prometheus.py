#!/usr/bin/env python
import ConfigParser
import collections
import json
import logging
import os
import re
import socket
import sys
import time
import pytz
from optparse import OptionParser
from multiprocessing import Process
from itertools import islice
from datetime import datetime
import dateutil
from dateutil.tz import tzlocal
import urlparse
import httplib
import requests
import statistics

'''
This script gathers data to send to Insightfinder
'''


def start_data_processing(thread_number):
    end_time = int(time.time())
    start_time = end_time - if_config_vars['run_interval']

    pid = 0 if 'METRIC' in if_config_vars['project_type'] else 1
    if len(if_config_vars['project_type']) > 1:
        pid = os.fork()
    if pid == 0:
        # metric
        logger.debug(str(os.getpid()) + ' is running the metric agent')
        dispatch_metric_agent(start_time, end_time)
    else:
        # alert agent
        logger.debug(str(os.getpid()) + ' is running the alert agent')
        dispatch_alert_agent(start_time)
    

def dispatch_metric_agent(start_time, end_time):
    # get all metrics from api if none specified
    if len(agent_config_vars['metrics_copy']) == 0:
        get_all_metrics()
    build_metric_name_map()
    prepare_metric_agent(start_time, end_time)
    print_summary_info()

    # send a query for each metric
    for query in agent_config_vars['metrics_copy']:
        query_string = query + agent_config_vars['query_label_selector']
        agent_config_vars['api_parameters']['query'] = query_string
        response_json = call_prometheus_api()
        metrics = _get_json_field_helper(response_json, agent_config_vars['json_top_level'].split(JSON_LEVEL_DELIM), True)
        for metric in metrics:
           extract_metric(metric)


def build_metric_name_map():
    '''
    Contstructs a hash of <raw_metric_name>: <formatted_metric_name>
    '''
    # get metrics from the global
    metrics = agent_config_vars['metrics_copy']
    # initialize the hash of formatted names
    agent_config_vars['metrics_names'] = dict()
    tree = build_sentence_tree(metrics)
    min_tree = fold_up(tree)


def build_sentence_tree(sentences):
    '''
    Takes a list of sentences and builds a tree from the words
        I ate two red apples ----\                     /---> "red" ----> "apples" -> "_name" -> "I ate two red apples"
        I ate two green pears ----> "I" -> "ate" -> "two" -> "green" --> "pears" --> "_name" -> "I ate two green pears"
        I ate one yellow banana -/             \--> "one" -> "yellow" -> "banana" -> "_name" -> "I ate one yellow banana"
    '''
    tree = dict()
    for sentence in sentences:
        words = format_sentence(sentence)
        current_path = tree
        for word in words:
            if word not in current_path:
                current_path[word] = dict()
            current_path = current_path[word]
        # add a terminal _name node with the raw sentence as the value
        current_path['_name'] = sentence
    return tree


def format_sentence(sentence):
    '''
    Takes a sentence and chops it into an array by word
    Implementation-specifc
    '''
    words = sentence.strip(':')
    words = COLONS.sub('/', words)
    words = UNDERSCORE.sub('/', words)
    words = words.split('/')
    return words


def fold_up(sentence_tree):
    '''
    Entry point for fold_up. See fold_up_helper for details
    '''
    folded = dict()
    for node_name in sentence_tree:
        fold_up_helper(folded, node_name, sentence_tree[node_name])
    return folded


def fold_up_helper(current_path, node_name, node):
    '''
    Recursively build a new sentence tree, where, 
        for each node that has only one child,
        that child is "folded up" into its parent.
    The tree therefore treats unique phrases as words s.t.
                      /---> "red apples"
        "I ate" -> "two" -> "green pears"
             \---> "one" -> "yellow banana"
    As a side effect, if there are terminal '_name' nodes,
        this also builds a hash in 
            agent_config_vars['metrics_names']
        of raw_name = formatted name, as this was
        probably the goal of calling this in the first place.
    '''
    while len(node.keys()) == 1 or '_name' in node.keys():
        keys = node.keys()
        # if we've reached a terminal end
        if '_name' in keys:
            agent_config_vars['metrics_names'][node['_name']] = node_name
            keys.remove('_name')
            node.pop('_name')
        # if there's still a single key path to follow
        if len(keys) == 1:
            next_key = keys[0]
            node_name += '_' + next_key
            node = node[next_key]
        else:
            break
    current_path[node_name] = node
    for node_nested in node:
        if node_nested == '_name':
            agent_config_vars['metrics_names'][node[node_nested]] = node_name
        else:
            fold_up_helper(current_path, node_name + '/' + node_nested, node[node_nested])


def extract_metric(metric):
    metric_name = get_json_field(metric, 'metric_name_field')
    if metric_name in agent_config_vars['metrics_to_ignore']:
        return
    metric_name = agent_config_vars['metrics_names'][metric_name] 
    agent_config_vars['data_fields'] = [metric_name]
    logger.debug(metric_name)
    # add host and device
    metric.update(extract_host(metric['metric']))
    metric.update(extract_device(metric['metric']))
    if 'value' in metric: # vector
        logger.debug('vector')
        metric.update(extract_time_and_value(metric['value'], metric_name))
        parse_json_message_single(metric)
    elif 'values' in metric: # matrix
        logger.debug('matrix')
        for value in metric['values']:
            metric.update(extract_time_and_value(value, metric_name))
            parse_json_message_single(metric)


def extract_host(metric):
    host_value = get_json_field_by_pri(metric, ['node', 'host', 'host_ip', 'instance', 'address'])
    parsed = urlparse.urlparse(host_value)
    host = parsed.hostname or parsed.path.split(':')[0]
    return {'_host': host}
    

def extract_device(metric):
    container = get_json_field_by_pri(metric, ['container', 'container_id', 'image_id'])
    container = SLASHES.sub(r"\\", container)
    dev_pod = get_json_field_by_pri(metric, ['device', 'pod' ,'pod_ip'])
    dev_pod = SLASHES.sub(r"\\", dev_pod)
    group = get_json_field_by_pri(metric, ['deployment', 'service', 'job', 'namespace'])
    group = SLASHES.sub(r"\\", group)
    # default to most granular level
    device = container or dev_pod or group
    if group and dev_pod:
        device = group + '/' + dev_pod
    elif group and container:
        device = group + '/' + container
    elif dev_pod and container:
        device = dev_pod + '/' + container
    return {'_device': device}

    
def extract_time_and_value(to_extract, metric_name):
    return {'_time': to_extract[0], metric_name: to_extract[1]}
    

def prepare_metric_queries():
    for metric in agent_config_vars['metrics_copy']:
        query_string = metric + agent_config_vars['query_label_selector']


def prepare_metric_agent(start_time, end_time):
    if_config_vars['project_type'] = 'METRIC'

    agent_config_vars['api_endpoint'] = 'query_range'
    agent_config_vars['api_parameters']['start'] = start_time
    agent_config_vars['api_parameters']['end'] = end_time
    agent_config_vars['api_parameters']['step'] = if_config_vars['sampling_interval']
    agent_config_vars['json_top_level'] = 'result'
    agent_config_vars['project_field'] = '' # metric.namespace
    agent_config_vars['instance_field'] = '_host' # see extract_host()
    agent_config_vars['device_field'] = '_device' # see extract_device()
    agent_config_vars['timestamp_field'] = '_time' # will need to parse from ['value'][0] or ['values'][i][0]
    agent_config_vars['timestamp_format'] = 'epoch'
    agent_config_vars['metric_name_field'] = 'metric.__name__' # will need to parse from ['value'][1] or ['values'][i][1]

    agent_config_vars['filters_include'] = ''
    agent_config_vars['filters_exclude'] = ''


def get_all_metrics():
    agent_config_vars['api_endpoint'] = 'label/__name__/values'
    metrics_list = call_prometheus_api()
    agent_config_vars['metrics'] = metrics_list
    agent_config_vars['metrics_copy'] = metrics_list


def dispatch_alert_agent(time):
    prepare_alert_agent()
    print_summary_info()
    response_json = call_prometheus_api()
    alerts = _get_json_field_helper(response_json, agent_config_vars['json_top_level'].split(JSON_LEVEL_DELIM), True)
    for alert in alerts:
        alert[agent_config_vars['timestamp_field']] = get_timestamp_from_date_string(alert[agent_config_vars['timestamp_field']].split('.')[0])
    agent_config_vars['timestamp_format'] = 'epoch'
    alerts_sorted = sorted(alerts, key=lambda alert: alert[agent_config_vars['timestamp_field']])
    for alert in alerts_sorted:
        if alert[agent_config_vars['timestamp_field']] < time:
            break
        parse_json_message_single(alert)


def prepare_alert_agent():
    if 'METRIC' in if_config_vars['project_type']:
        if_config_vars['project_type'].remove('METRIC')
    if_config_vars['project_type'] = if_config_vars['project_type'][0]
    if_config_vars['project_name'] = if_config_vars['project_name_alert']
    
    agent_config_vars['api_endpoint'] = 'alerts'
    agent_config_vars['json_top_level'] = 'alerts'
    agent_config_vars['timestamp_field'] = 'activeAt'
    agent_config_vars['timestamp_format'] = '%Y-%m-%dT%H:%M:%S'


def call_prometheus_api():
    logger.debug(agent_config_vars['api_parameters'])
    response_raw = send_request(make_api_req_url(), params=agent_config_vars['api_parameters'], proxies=agent_config_vars['proxies'])
    response_json = json.loads(response_raw.text)
    return check_api_call_success(response_json)


def check_api_call_success(response_json):
    data = _get_json_field_helper(response_json, ['data'], True)
    if _get_json_field_helper(response_json, ['status'], False) == 'success' and len(data) != 0:
        return data
    else:
        logger.error('Error when contacting api at ' + str(make_api_req_url()))
        sys.exit(1)


def get_agent_config_vars():
    """ Read and parse config.ini """
    if os.path.exists(os.path.abspath(os.path.join(__file__, os.pardir, 'config.ini'))):
        config_parser = ConfigParser.SafeConfigParser()
        config_parser.read(os.path.abspath(os.path.join(__file__, os.pardir, 'config.ini')))
        try:
            # uri
            prometheus_uri = config_parser.get('agent', 'prometheus_uri')

            # metrics
            query_label_selector = config_parser.get('agent', 'query_label_selector')
            metrics = config_parser.get('agent', 'metrics')
            metrics_to_ignore = config_parser.get('agent', 'metrics_to_ignore')

            # alerts
            data_fields = config_parser.get('agent', 'alert_data_fields')
            filters_include = config_parser.get('agent', 'alert_filters_include')
            filters_exclude = config_parser.get('agent', 'alert_filters_exclude')

            # proxies
            agent_http_proxy = config_parser.get('agent', 'agent_http_proxy')
            agent_https_proxy = config_parser.get('agent', 'agent_https_proxy')
                    
        except ConfigParser.NoOptionError:
            logger.error('Agent not correctly configured. Check config file.')
            sys.exit(1)
        
        # api
        if len(prometheus_uri) == 0:
            logger.error('Agent not correctly configured (history_server_uri). Check config file.')
            sys.exit(1)
        api_url = urlparse.urljoin(prometheus_uri, '/api/v1/')

        # proxies
        agent_proxies = dict()
        if len(agent_http_proxy) > 0:
            agent_proxies['http'] = agent_http_proxy
        if len(agent_https_proxy) > 0:
            agent_proxies['https'] = agent_https_proxy
        
        # metrics
        if len(metrics) != 0:
            metrics = metrics.split(',')

        if len(metrics_to_ignore) != 0:
            metrics_to_ignore = metrics_to_ignore.split(',')

        # alert data fields
        if len(data_fields) != 0:
            data_fields = data_fields.split(',')

        # filters
        if len(filters_include) != 0:
            filters_include = filters_include.split('|')
        if len(filters_exclude) != 0:
            filters_exclude = filters_exclude.split('|')

        # add parsed variables to a global
        config_vars = {
            'query_label_selector': query_label_selector,
            'proxies': agent_proxies,
            'api_url': api_url,
            'api_parameters': dict(),
            'filters_include': filters_include,
            'filters_exclude': filters_exclude,
            'data_format': 'JSON',
            'json_top_level': '',
            'project_field': '',
            'instance_field': '',
            'data_fields': data_fields,
            'device_field': '',
            'metrics': metrics,
            'metrics_copy': list(metrics),
            'metrics_to_ignore': metrics_to_ignore,
            'timestamp_field': '',
            'timestamp_format': '',
            'strip_tz': False,
            'strip_tz_fmt': ''
        }

        return config_vars
    else:
        logger.warning('No config file found. Exiting...')
        exit()


########################
# Start of boilerplate #
########################
def get_if_config_vars():
    """ get config.ini vars """
    if os.path.exists(os.path.abspath(os.path.join(__file__, os.pardir, 'config.ini'))):
        config_parser = ConfigParser.SafeConfigParser()
        config_parser.read(os.path.abspath(os.path.join(__file__, os.pardir, 'config.ini')))
        try:
            user_name = config_parser.get('insightfinder', 'user_name')
            license_key = config_parser.get('insightfinder', 'license_key')
            token = config_parser.get('insightfinder', 'token')
            project_name = config_parser.get('insightfinder', 'project_name')
            project_name_alert = config_parser.get('insightfinder', 'project_name_alert')
            project_type = config_parser.get('insightfinder', 'project_type').upper()
            sampling_interval = config_parser.get('insightfinder', 'sampling_interval')
            run_interval = config_parser.get('insightfinder', 'run_interval')
            chunk_size_kb = config_parser.get('insightfinder', 'chunk_size_kb')
            if_url = config_parser.get('insightfinder', 'if_url')
            if_http_proxy = config_parser.get('insightfinder', 'if_http_proxy')
            if_https_proxy = config_parser.get('insightfinder', 'if_https_proxy')
        except ConfigParser.NoOptionError:
            logger.error('Agent not correctly configured. Check config file.')
            sys.exit(1)

        # check required variables
        if len(user_name) == 0:
            logger.warning('Agent not correctly configured (user_name). Check config file.')
            sys.exit(1)
        if len(license_key) == 0:
            logger.warning('Agent not correctly configured (license_key). Check config file.')
            sys.exit(1)
        if len(project_name) == 0:
            logger.warning('Agent not correctly configured (project_name). Check config file.')
            sys.exit(1)
        if len(project_type) == 0:
            logger.warning('Agent not correctly configured (project_type). Check config file.')
            sys.exit(1)
        
        project_type = project_type.split(',')
        for p_type in project_type:
            if p_type not in {
                    'METRIC',
                    'LOG',                 
                    'INCIDENT',
                    'ALERT',
                    'DEPLOYMENT'
                    }:
               logger.warning('Agent not correctly configured (project_type). Check config file.')
               sys.exit(1)  

        if len(sampling_interval) == 0:
            if 'METRIC' in project_type:
                logger.warning('Agent not correctly configured (sampling_interval). Check config file.')
                sys.exit(1)
            else:
                # set default for non-metric
                sampling_interval = 1

        if len(run_interval) == 0:
            logger.warning('Agent not correctly configured (run_interval). Check config file.')
            sys.exit(1)

        if sampling_interval.endswith('s'):
            sampling_interval = int(sampling_interval[:-1])
        else:
            sampling_interval = int(sampling_interval) * 60

        if run_interval.endswith('s'):
            run_interval = int(run_interval[:-1])
        else:
            run_interval = int(run_interval) * 60

        # defaults
        if len(chunk_size_kb) == 0:
            chunk_size_kb = 2048  # 2MB chunks by default
        if len(if_url) == 0:
            if_url = 'https://app.insightfinder.com'

        # set IF proxies
        if_proxies = dict()
        if len(if_http_proxy) > 0:
            if_proxies['http'] = if_http_proxy
        if len(if_https_proxy) > 0:
            if_proxies['https'] = if_https_proxy

        config_vars = {
            'user_name': user_name,
            'license_key': license_key,
            'token': token,
            'project_name': project_name,
            'project_name_alert': project_name_alert,
            'project_type': project_type,
            'sampling_interval': int(sampling_interval),     # as seconds
            'run_interval': int(run_interval),               # as seconds
            'chunk_size': int(chunk_size_kb) * 1024,         # as bytes
            'if_url': if_url,
            'if_proxies': if_proxies
        }

        return config_vars
    else:
        logger.error('Agent not correctly configured. Check config file.')
        sys.exit(1)


def get_cli_config_vars():
    """ get CLI options. use of these options should be rare """
    usage = 'Usage: %prog [options]'
    parser = OptionParser(usage=usage)
    """
    ## not ready.
    parser.add_option('--threads', default=1,
                      action='store', dest='threads', help='Number of threads to run')
    """
    parser.add_option('--tz', default='UTC', action='store', dest='time_zone', 
                      help='Timezone of the data. See pytz.all_timezones')
    parser.add_option('-q', '--quiet', action='store_true', dest='quiet', 
                      help='Only display warning and error log messages')
    parser.add_option('-v', '--verbose', action='store_true', dest='verbose', 
                      help='Enable verbose logging')
    parser.add_option('-t', '--testing', action='store_true', dest='testing', 
                      help='Set to testing mode (do not send data).' +
                           ' Automatically turns on verbose logging')
    (options, args) = parser.parse_args()

    """
    # not ready
    try:
        threads = int(options.threads)
    except ValueError:
        threads = 1
    """

    config_vars = {
        'threads': 1,
        'testing': False,
        'log_level': logging.INFO,
        'time_zone': pytz.utc
        }

    if options.testing:
        config_vars['testing'] = True

    if options.verbose or options.testing:
        config_vars['log_level'] = logging.DEBUG
    elif options.quiet:
        config_vars['log_level'] = logging.WARNING

    if len(options.time_zone) != 0 and options.time_zone in pytz.all_timezones:
        config_vars['time_zone'] = pytz.timezone(options.time_zone)

    return config_vars


def strip_tz_info(timestamp_format):
    # strptime() doesn't allow timezone info
    if '%Z' in timestamp_format:
        position = timestamp_format.index('%Z')
        strip_tz_fmt = PCT_Z_FMT
    if '%z' in timestamp_format:
        position = timestamp_format.index('%z')
        strip_tz_fmt = PCT_z_FMT
    
    if len(timestamp_format) > (position + 2):
        timestamp_format = timestamp_format[:position] + timestamp_format[position+2:]
    else:
        timestamp_format = timestamp_format[:position]
    if cli_config_vars['time_zone'] == pytz.timezone('UTC'):
        logger.warning('Time zone info will be stripped from timestamps, but no time zone info was supplied in the config. Assuming UTC')
    
    return {'strip_tz': True, 'strip_tz_fmt': strip_tz_fmt, 'timestamp_format': timestamp_format}


def check_csv_fieldnames(csv_field_names, all_fields):
    # required
    for field in all_fields['required_fields']:
        all_fields['required_fields'][field]['index'] = get_field_index(csv_field_names, all_fields['optional_fields'][field]['name'], field, True)

    # optional
    for field in all_fields['optional_fields']:
        if len(all_fields['optional_fields'][field]['name']) != 0:
            index = get_field_index(csv_field_names, all_fields['optional_fields'][field]['name'], field)
            if isinstance(index, int):
                all_fields['optional_fields'][field]['index'] = index
            else:
                all_fields['optional_fields'][field]['index'] = ''

    # filters
    for field in all_fields['filters']:
        if len(all_fields['filters'][field]['name']) != 0:
            filters_temp = []
            for _filter in all_fields['filters'][field]['name']:
                filter_field = _filter.split(':')[0]          
                filter_vals = _filter.split(':')[1]
                filter_index = get_field_index(csv_field_names, filter_field, field)
                if isinstance(filter_index, int):
                    filter_temp = str(filter_index) + ':' + filter_vals
                    filters_temp.append(filter_temp)
            all_fields['filters'][field]['filter'] = filters_temp

    # data
    data_fields = all_fields['data_fields']
    if len(all_fields['data_fields']) != 0:
        data_fields_temp = []
        for data_field in all_fields['data_fields']:
            data_field_temp = get_field_index(csv_field_names, data_field, 'data_field')
            if isinstance(data_field_temp, int):
                data_fields_temp.append(data_field_temp)
        all_fields['data_fields'] = data_fields_temp
    if len(all_fields['data_fields']) == 0:
        # use all non-timestamp fields
        all_fields['data_fields'] = range(len(csv_field_names))

    return all_fields


def make_api_req_url():
    return urlparse.urljoin(agent_config_vars['api_url'], agent_config_vars['api_endpoint'])


def check_project(project_name):
    if 'token' in if_config_vars and len(if_config_vars['token']) != 0:
        logger.debug(project_name)
        try:
            # check for existing project
            check_url = urlparse.urljoin(if_config_vars['if_url'], '/api/v1/getprojectstatus')
            output_check_project = subprocess.check_output('curl "' + check_url + '?userName=' + if_config_vars['user_name'] + '&token=' + if_config_vars['token'] + '&projectList=%5B%7B%22projectName%22%3A%22' + project_name + '%22%2C%22customerName%22%3A%22' + if_config_vars['user_name'] + '%22%2C%22projectType%22%3A%22CUSTOM%22%7D%5D&tzOffset=-14400000"', shell=True)
            # create project if no existing project
            if project_name not in output_check_project:
                logger.debug('creating project')
                create_url = urlparse.urljoin(if_config_vars['if_url'], '/api/v1/add-custom-project')
                output_create_project = subprocess.check_output('no_proxy= curl -d "userName=' + if_config_vars['user_name'] + '&token=' + if_config_vars['token'] + '&projectName=' + project_name + '&instanceType=PrivateCloud&projectCloudType=PrivateCloud&dataType=' + get_data_type_from_project_type() + '&samplingInterval=' + str(if_config_vars['sampling_interval'] / 60) +  '&samplingIntervalInSeconds=' + str(if_config_vars['sampling_interval']) + '&zone=&email=&access-key=&secrete-key=&insightAgentType=' + get_insight_agent_type_from_project_type() + '" -H "Content-Type: application/x-www-form-urlencoded" -X POST ' + create_url + '?tzOffset=-18000000', shell=True)
            # set project name to proposed name
            if_config_vars['project_name'] = project_name
            # try to add new project to system
            if 'system_name' in if_config_vars and len(if_config_vars['system_name']) != 0:
                system_url = urlparse.urljoin(if_config_vars['if_url'], '/api/v1/projects/update')
                output_update_project = subprocess.check_output('no_proxy= curl -d "userName=' + if_config_vars['user_name'] + '&token=' + if_config_vars['token'] + '&operation=updateprojsettings&projectName=' + project_name + '&systemName=' + if_config_vars['system_name'] + '" -H "Content-Type: application/x-www-form-urlencoded" -X POST ' + system_url + '?tzOffset=-18000000', shell=True)
        except subprocess.CalledProcessError as e:
            logger.error('Unable to create project for ' + project_name + '. Data will be sent to ' + if_config_vars['project_name'])


def get_field_index(field_names, field, label, is_required=False):
    err_code = ''
    try:
        temp = int(field)
        if temp > len(field_names):
            err_msg = 'field ' + str(field) + ' is not a valid array index given field names ' + str(field_names)
            field = err_code
        else:
            field = temp
    # not an integer
    except ValueError:
        try:
            field = field_names.index(field)
        # not in the field names
        except ValueError:
            err_msg = 'field ' + str(field) + ' is not a valid field in field names ' + str(field_names)
            field = err_code
    finally:
        if field == err_code:
            logger.warn('Agent not configured correctly (' + label + ')\n' + err_msg)
            if is_required:
                sys.exit(1)
            return
        else:
            return field


def should_include_per_config(setting, value):
    """ determine if an agent config filter setting would exclude a given value """
    return len(agent_config_vars[setting]) != 0 and value not in agent_config_vars[setting]


def should_exclude_per_config(setting, value):
    """ determine if an agent config exclude setting would exclude a given value """
    return len(agent_config_vars[setting]) != 0 and value in agent_config_vars[setting]


def get_json_size_bytes(json_data):
    """ get size of json object in bytes """
    return len(bytearray(json.dumps(json_data)))


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for index in xrange(0, len(l), n):
        yield l[index:index + n]


def chunk_map(data, SIZE=50):
    """Yield successive n-sized chunks from l."""
    it = iter(data)
    for i in xrange(0, len(data), SIZE):
        yield {k: data[k] for k in islice(it, SIZE)}


def get_file_list_for_directory(root_path):
    file_list = []
    for path, subdirs, files in os.walk(root_path):
        for name in files:
            file_list.append(os.path.join(path, name))
    return file_list


def parse_raw_message(message):
    if 'METRIC' in if_config_vars['project_type']:
        metric_handoff(*raw_parse_metric(message))
    else:
        log_handoff(*raw_parse_log(message))


def get_json_field(message, config_setting, default=''):
    '''
    Get the value of a JSON field based on agent config setting, return default if unable to get value
    '''
    if len(agent_config_vars[config_setting]) == 0:
        return default
    field_val = json_format_field_value(
                    _get_json_field_helper(message, agent_config_vars[config_setting].split(JSON_LEVEL_DELIM)))
    if len(field_val) == 0:
        field_val = default
        logger.warning('using default value for ' + config_setting)
    return field_val


def get_json_field_by_pri(message, pri_list):
    value = ''
    while value == '' and len(pri_list) != 0:
        value = _get_json_field_helper(message, pri_list.pop(0).split(JSON_LEVEL_DELIM))
    return value


def _get_json_field_helper(nested_value, next_fields, allow_list=False):
    '''
    Recursively get the next field in nested json.
    !! This is the one you want to use to get the raw value of a field !!
    '''
    if len(next_fields) == 0:
        return ''
    elif isinstance(nested_value, list):
        return json_gather_list_values(nested_value, next_fields)
    elif not isinstance(nested_value, dict):
        return ''
    next_field = next_fields.pop(0)
    next_value = nested_value.get(next_field)
    if next_value is None:
        return ''
    elif len(bytes(next_value)) == 0:
        return ''
    elif next_fields is None:
        return next_value
    elif len(next_fields) == 0:
        return next_value
    elif isinstance(next_value, set):
        next_value_all = ''
        for item in next_value:
            next_value_all += str(item)
        return next_value_all
    elif isinstance(next_value, list):
        if allow_list: 
            return json_gather_list_values(next_value, next_fields)
        else:
            raise Exception('encountered list in json when not allowed')
            return ''
    elif isinstance(next_value, dict):
        return _get_json_field_helper(next_value, next_fields, allow_list)
    else:
        logger.debug('given field could not be found')
        return ''


def json_gather_list_values(l, fields):
    sub_field_value = []
    for sub_value in l:
        fields_copy = list(fields_copy)
        json_value = json_format_field_value(_get_json_field_helper(sub_value, fields_copy, True))
        if len(json_value) != 0:
            sub_field_value.append(json_value)
    return sub_field_value


def json_format_field_value(value):
    if isinstance(value, (dict, list)):
        if len(value) == 1 and isinstance(value, list):
            return value.pop(0)
        return value
    return str(value)


def parse_json_message(messages):
    if len(agent_config_vars['json_top_level']) != 0:
        if agent_config_vars['json_top_level'] == '[]' and isinstance(messages, list):
            for message in messages:
                parse_json_message_single(message)
        else:
            top_level = _get_json_field_helper(messages, agent_config_vars['json_top_level'].split(JSON_LEVEL_DELIM), True)
            if isinstance(top_level, list):
                for message in top_level:
                    parse_json_message_single(message)
            else:
                parse_json_message_single(top_level)
    else:
        parse_json_message_single(messages)


def parse_json_message_single(message):
    # filter
    if len(agent_config_vars['filters_include']) != 0:
        # for each provided filter
        is_valid = False
        for _filter in agent_config_vars['filters_include']:
            filter_field = _filter.split(':')[0]
            filter_vals = _filter.split(':')[1].split(',')
            filter_check = str(_get_json_field_helper(message, filter_field.split(JSON_LEVEL_DELIM), True))
            # check if a valid value
            for filter_val in filter_vals:
                if filter_val.upper() in filter_check.upper():
                    is_valid = True
                    break
            if is_valid:
                break
        if not is_valid:
            logger.debug('filtered message (inclusion): ' + filter_check + ' not in ' + str(filter_vals))
            return
        else:
            logger.debug('passed filter (inclusion)')
            
    if len(agent_config_vars['filters_exclude']) != 0:
        # for each provided filter
        for _filter in agent_config_vars['filters_exclude']:
            filter_field = _filter.split(':')[0]
            filter_vals = _filter.split(':')[1].split(',')
            filter_check = str(_get_json_field_helper(message, filter_field.split(JSON_LEVEL_DELIM), True))
            # check if a valid value
            for filter_val in filter_vals:
                if filter_val.upper() in filter_check.upper():
                    logger.debug('filtered message (exclusion): ' + str(filter_val) + ' in ' + str(filter_check))
                    return
        logger.debug('passed filter (exclusion)')

    # get project, instance, & device
    # check_project(get_json_field(message, 'project_field', if_config_vars['project_name']))
    instance = get_json_field(message, 'instance_field', HOSTNAME)
    device = get_json_field(message, 'device_field')

    # get timestamp
    timestamp = get_json_field(message, 'timestamp_field')
    timestamp = get_timestamp_from_date_string(timestamp)
    message.pop(agent_config_vars['timestamp_field'])

    logger.debug(instance)
    logger.debug(device)
    logger.debug(timestamp)
    # get data
    log_data = dict()
    if len(agent_config_vars['data_fields']) != 0: 
        logger.debug(agent_config_vars['data_fields'])
        for data_field in agent_config_vars['data_fields']:
            data_value = json_format_field_value(_get_json_field_helper(message, data_field.split(JSON_LEVEL_DELIM), True))
            if len(data_value) != 0:
                if 'METRIC' in if_config_vars['project_type']:
                    logger.debug('adding metric data')
                    metric_handoff(timestamp, data_field.replace('.', '/'), data_value, instance, device)
                else:
                    logger.debug('adding log data')
                    log_data[data_field.replace('.', '/')] = data_value
    else:    
        logger.debug('no data_fields defined')
        if 'METRIC' in if_config_vars['project_type']:
            # assume metric data is in top level
            for data_field in message:
                data_value = str(_get_json_field_helper(message, data_field.split(JSON_LEVEL_DELIM), True))
                if data_value is not None:
                    metric_handoff(timestamp, data_field.replace('.', '/'), data_value, instance, device)
                    logger.debug('adding metric data')
        else:
            log_data = message
            logger.debug('adding log data')

    # hand off to log
    if 'METRIC' not in if_config_vars['project_type']:
        log_handoff(timestamp, log_data, instance, device)


def parse_csv_message(message):
    # filter
    if len(agent_config_vars['filters_include']) != 0:
        # for each provided filter, check if there are any allowed valued
        is_valid = False
        for _filter in agent_config_vars['filters_include']:          
            filter_field = _filter.split(':')[0]              
            filter_vals = _filter.split(':')[1].split(',')    
            filter_check = message[int(filter_field)]
            # check if a valid value                          
            for filter_val in filter_vals:
                if filter_val.upper() not in filter_check.upper():
                    is_valid = True
                    break
            if is_valid:
                break
        if not is_valid:
            logger.debug('filtered message (inclusion): ' + filter_check + ' not in ' + str(filter_vals))
            return                                        
        else:
            logger.debug('passed filter (inclusion)')

    if len(agent_config_vars['filters_exclude']) != 0:
        # for each provided filter, check if there are any disallowed values
        for _filter in agent_config_vars['filters_exclude']:          
            filter_field = _filter.split(':')[0]              
            filter_vals = _filter.split(':')[1].split(',')    
            filter_check = message[int(filter_field)]
            # check if a valid value                          
            for filter_val in filter_vals:
                if filter_val.upper() in filter_check.upper():
                    logger.debug('filtered message (exclusion): ' + filter_check + ' in ' + str(filter_vals))
                    return                                        
        logger.debug('passed filter (exclusion)')

    # project
    # if isinstance(agent_config_vars['project_field'], int):
    #    check_project(message[agent_config_vars['project_field']])

    # instance
    instance = HOSTNAME
    if isinstance(agent_config_vars['instance_field'], int):
        instance = message[agent_config_vars['instance_field']]

    # device
    device = ''
    if isinstance(agent_config_vars['device_field'], int):
        device = message[agent_config_vars['device_field']]

    # data & timestamp
    columns = [agent_config_vars['timestamp_field']] + agent_config_vars['data_fields']
    row = list(message[i] for i in columns)
    fields = list(agent_config_vars['csv_field_names'][j] for j in agent_config_vars['data_fields'])
    parse_csv_row(row, fields, instance, device)
    

def parse_csv_data(csv_data, instance, device=''):
    """
    parses CSV data, assuming the format is given as:
        header row:  timestamp,field_1,field_2,...,field_n
        n data rows: TIMESTAMP,value_1,value_2,...,value_n
    """

    # get field names from header row
    field_names = csv_data.pop(0).split(CSV_DELIM)[1:]

    # go through each row
    for row in csv_data:
        if len(row) > 0:
            parse_csv_row(row.split(CSV_DELIM), field_names, instance, device)


def parse_csv_row(row, field_names, instance, device=''):
    timestamp = get_timestamp_from_date_string(row.pop(0))
    if 'METRIC' in if_config_vars['project_type']:
        for i in range(len(row)):
            metric_handoff(timestamp, field_names[i], row[i], instance, device)
    else:
        json_message = dict()
        for i in range(len(row)):
            json_message[field_names[i]] = row[i]
        log_handoff(timestamp, json_message, instance, device)
            

def get_timestamp_from_date_string(date_string):
    """ parse a date string into unix epoch (ms) """
    if 'strip_tz' in agent_config_vars and agent_config_vars['strip_tz']:
        date_string = ''.join(agent_config_vars['strip_tz_fmt'].split(date_string))

    if 'timestamp_format' in agent_config_vars:
        if agent_config_vars['timestamp_format'] == 'epoch':
            timestamp_datetime = get_datetime_from_unix_epoch(date_string)
        else:
            timestamp_datetime = datetime.strptime(date_string, agent_config_vars['timestamp_format'])
    else:
        try:
            timestamp_datetime = dateutil.parse.parse(date_string)
        except:
            timestamp_datetime = get_datetime_from_unix_epoch(date_string)
            agent_config_vars['timestamp_format'] = 'epoch'

    timestamp_localize = cli_config_vars['time_zone'].localize(timestamp_datetime)

    epoch = long((timestamp_localize - datetime(1970, 1, 1, tzinfo=pytz.utc)).total_seconds()) * 1000
    return epoch


def get_datetime_from_unix_epoch(date_string):
    try:
        # strip leading whitespace and zeros
        epoch = date_string.lstrip(' 0')
        # roughly check for a timestamp between ~1973 - ~2286
        if len(epoch) in range(13, 15):
            epoch = int(epoch) / 1000
        elif len(epoch) in range(9, 12):
            epoch = int(epoch)

        return datetime.fromtimestamp(epoch)
    except ValueError:
        # if the date cannot be converted into a number by built-in long()
        logger.warn('Date format not defined & data does not look like unix epoch: ' + date_string)
        sys.exit(1)


def make_safe_instance_string(instance, device=''):
    """ make a safe instance name string, concatenated with device if appropriate """
    # strip underscore and colon
    instance = UNDERSCORE.sub('.', instance)
    instance = COLONS.sub('-', instance)
    # if there's a device, concatenate it to the instance with an underscore
    if len(device) != 0:
        instance = make_safe_instance_string(device) + '_' + instance
    return instance


def make_safe_metric_key(metric):
    """ make safe string already handles this """
    metric = LEFT_BRACE.sub('(', metric)
    metric = RIGHT_BRACE.sub(')', metric)
    metric = PERIOD.sub('/', metric)
    metric = COLONS.sub('/', metric)
    return metric


def make_safe_string(string):
    """
    Take a single string and return the same string with spaces, slashes,
    underscores, and non-alphanumeric characters subbed out.
    """
    string = SPACES.sub('-', string)
    string = SLASHES.sub('.', string)
    string = UNDERSCORE.sub('.', string)
    string = NON_ALNUM.sub('', string)
    return string


def set_logger_config(level):
    """ set up logging according to the defined log level """
    # Get the root logger
    logger_obj = logging.getLogger(__name__)
    # Have to set the root logger level, it defaults to logging.WARNING
    logger_obj.setLevel(level)
    # route INFO and DEBUG logging to stdout from stderr
    logging_handler_out = logging.StreamHandler(sys.stdout)
    logging_handler_out.setLevel(logging.DEBUG)
    # create a logging format
    formatter = logging.Formatter('%(asctime)s [pid %(process)d] %(levelname)-8s %(module)s.%(funcName)s():%(lineno)d %(message)s')
    logging_handler_out.setFormatter(formatter)
    logger_obj.addHandler(logging_handler_out)

    logging_handler_err = logging.StreamHandler(sys.stderr)
    logging_handler_err.setLevel(logging.WARNING)
    logger_obj.addHandler(logging_handler_err)
    return logger_obj


def print_summary_info():
    # info to be sent to IF
    post_data_block = '\nIF settings:'
    for i in sorted(if_config_vars.keys()):
        post_data_block += '\n\t' + i + ': ' + str(if_config_vars[i])
    logger.debug(post_data_block)

    # variables from agent-specific config
    agent_data_block = '\nAgent settings:'
    for j in sorted(agent_config_vars.keys()):
        agent_data_block += '\n\t' + j + ': ' + str(agent_config_vars[j])
    logger.debug(agent_data_block)

    # variables from cli config
    cli_data_block = '\nCLI settings:'
    for k in sorted(cli_config_vars.keys()):
        cli_data_block += '\n\t' + k + ': ' + str(cli_config_vars[k])
    logger.debug(cli_data_block)


def initialize_data_gathering(thread_number):
    reset_track()
    track['chunk_count'] = 0
    track['entry_count'] = 0

    start_data_processing(thread_number)

    # last chunk
    if len(track['current_row']) > 0 or len(track['current_dict']) > 0:
        logger.debug('Sending last chunk')
        send_data_wrapper()


    logger.debug('Total chunks created: ' + str(track['chunk_count']))
    logger.debug('Total ' + if_config_vars['project_type'].lower() + ' entries: ' + str(track['entry_count']))


def reset_track():
    """ reset the track global for the next chunk """
    track['start_time'] = time.time()
    track['line_count'] = 0
    track['current_row'] = []
    track['current_dict'] = dict()


#########################################
# Functions to handle Log/Incident data #
#########################################
def incident_handoff(timestamp, data, instance, device=''):
    log_handoff(timestamp, data, instance, device)


def deployment_handoff(timestamp, data, instance, device=''):
    log_handoff(timestamp, data, instance, device)


def alert_handoff(timestamp, data, instance, device=''):
    log_handoff(timestamp, data, instance, device)


def log_handoff(timestamp, data, instance, device=''):
    entry = prepare_log_entry(str(int(timestamp)), data, instance, device)
    track['current_row'].append(entry)
    track['line_count'] += 1
    track['entry_count'] += 1
    if get_json_size_bytes(track['current_row']) >= if_config_vars['chunk_size'] or (time.time() - track['start_time']) >= if_config_vars['sampling_interval']:
        send_data_wrapper()
    elif track['entry_count'] % 100 == 0:
        logger.debug('Current data object size: ' + str(get_json_size_bytes(track['current_row'])) + ' bytes')


def prepare_log_entry(timestamp, data, instance, device=''):
    """ creates the log entry """
    entry = dict()
    entry['data'] = data
    if 'INCIDENT' in if_config_vars['project_type'] or 'DEPLOYMENT' in if_config_vars['project_type']:
        entry['timestamp'] = timestamp
        entry['instanceName'] = make_safe_instance_string(instance, device)
    else: # LOG or ALERT
        entry['eventId'] = timestamp
        entry['tag'] = make_safe_instance_string(instance, device)
    return entry


###################################
# Functions to handle Metric data #
###################################
def metric_handoff(timestamp, field_name, data, instance, device=''):
    append_metric_data_to_entry(timestamp, field_name, data, instance, device)
    track['entry_count'] += 1
    if get_json_size_bytes(track['current_dict']) >= if_config_vars['chunk_size'] or (time.time() - track['start_time']) >= if_config_vars['sampling_interval']:
        send_data_wrapper()
    elif track['entry_count'] % 500 == 0:
        logger.debug('Current data object size: ' + str(get_json_size_bytes(track['current_dict'])) + ' bytes')


def append_metric_data_to_entry(timestamp, field_name, data, instance, device=''):
    """ creates the metric entry """
    key = make_safe_metric_key(field_name) + '[' + make_safe_instance_string(instance, device) + ']'
    ts_str = str(timestamp)
    if ts_str not in track['current_dict']:
        track['current_dict'][ts_str] = dict()
    current_obj = track['current_dict'][ts_str]

    # use the next non-null value to overwrite the prev value
    # for the same metric in the same timestamp
    if key in current_obj.keys():
        if data is not None and len(str(data)) > 0:
            current_obj[key] += '|' + str(data)
    else:
        current_obj[key] = str(data)
    track['current_dict'][ts_str] = current_obj


def transpose_metrics():
    """ flatten data up to the timestamp"""
    for timestamp in track['current_dict'].keys():
        logger.debug(timestamp)
        track['line_count'] += 1
        new_row = dict()
        new_row['timestamp'] = timestamp
        for key in track['current_dict'][timestamp]:
            value = track['current_dict'][timestamp][key]
            if '|' in value:
                value = statistics.median(map(lambda v: float(v), value.split('|')))
            new_row[key] = str(value)
        track['current_row'].append(new_row)


################################
# Functions to send data to IF #
################################
def send_data_wrapper():
    """ wrapper to send data """
    if 'METRIC' in if_config_vars['project_type']:
        transpose_metrics()
    logger.debug('--- Chunk creation time: %s seconds ---' % round(time.time() - track['start_time'], 2))
    send_data_to_if(track['current_row'])
    track['chunk_count'] += 1
    reset_track()


def send_data_to_if(chunk_metric_data):
    send_data_time = time.time()

    # prepare data for metric streaming agent
    data_to_post = initialize_api_post_data()
    if 'DEPLOYMENT' in if_config_vars['project_type'] or 'INCIDENT' in if_config_vars['project_type']:
        for chunk in chunk_metric_data:
            chunk['data'] = json.dumps(chunk['data'])
    data_to_post[get_data_field_from_project_type()] = json.dumps(chunk_metric_data)

    logger.debug('First:\n' + str(chunk_metric_data[0]))
    logger.debug('Last:\n' + str(chunk_metric_data[-1]))
    logger.debug('Total Data (bytes): ' + str(get_json_size_bytes(data_to_post)))
    logger.debug('Total Lines: ' + str(track['line_count']))

    # do not send if only testing
    if cli_config_vars['testing']:
        return

    # send the data
    post_url = urlparse.urljoin(if_config_vars['if_url'], get_api_from_project_type())
    send_request(post_url, 'POST', 'Could not send request to IF',
                 str(get_json_size_bytes(data_to_post)) + ' bytes of data are reported.',
                 data=data_to_post, proxies=if_config_vars['if_proxies'])
    logger.debug('--- Send data time: %s seconds ---' % round(time.time() - send_data_time, 2))


def send_request(url, mode='GET', failure_message='Failure!', success_message='Success!', **request_passthrough):
    """ sends a request to the given url """
    # determine if post or get (default)
    req = requests.get
    if mode.upper() == 'POST':
        req = requests.post

    for i in xrange(ATTEMPTS):
        try:
            response = req(url, **request_passthrough)
            if response.status_code == httplib.OK:
                logger.info(success_message)
                return response
            else:
                logger.warn(failure_message)
                logger.debug('Response Code: ' + str(response.status_code) + '\n' +
                             'TEXT: ' + str(response.text))
        # handle various exceptions
        except requests.exceptions.Timeout:
            logger.exception('Timed out. Reattempting...')
            continue
        except requests.exceptions.TooManyRedirects:
            logger.exception('Too many redirects.')
            break
        except requests.exceptions.RequestException as e:
            logger.exception('Exception ' + str(e))
            break

    logger.error('Failed! Gave up after %d attempts.', i)
    return -1


def get_data_type_from_project_type():
    if 'METRIC' in if_config_vars['project_type']:
        return 'Metric'
    elif 'LOG' in if_config_vars['project_type']:
        return 'Log'
    elif 'ALERT' in if_config_vars['project_type']:
        return 'Alert'
    elif 'INCIDENT' in if_config_vars['project_type']:
        return 'Incident'
    elif 'DEPLOYMENT' in if_config_vars['project_type']:
        return 'Deployment'
    else:
        logger.warning('Project Type not correctly configured')
        sys.exit(1)


def get_insight_agent_type_from_project_type():
    if 'containerize' in agent_config_vars and agent_config_vars['containerize']:
        if 'REPLAY' in if_config_vars['project_type']:
            return 'containerReplay'
        else:
            return 'containerStreaming'
    elif 'REPLAY' in if_config_vars['project_type']:
        if 'METRIC' in if_config_vars['project_type']:
            return 'MetricFile'
        else:
            return 'LogFile'
    else:
        return 'Custom'


def get_agent_type_from_project_type():
    """ use project type to determine agent type """
    if 'METRIC' in if_config_vars['project_type']:
        if 'REPLAY' in if_config_vars['project_type']:
            return 'MetricFileReplay'
        else:
            return 'CUSTOM'
    elif 'REPLAY' in if_config_vars['project_type']: # LOG, ALERT
        return 'LogFileReplay'
    else:
        return 'LogStreaming'
    # INCIDENT and DEPLOYMENT don't use this


def get_data_field_from_project_type():
    """ use project type to determine which field to place data in """
    # incident uses a different API endpoint
    if 'INCIDENT' in if_config_vars['project_type']:
        return 'incidentData'
    elif 'DEPLOYMENT' in if_config_vars['project_type']:
        return 'deploymentData'
    else: # MERTIC, LOG, ALERT
        return 'metricData'


def get_api_from_project_type():
    """ use project type to determine which API to post to """
    # incident uses a different API endpoint
    if 'INCIDENT' in if_config_vars['project_type']:
        return 'incidentdatareceive'
    elif 'DEPLOYMENT' in if_config_vars['project_type']:
        return 'deploymentEventReceive'
    else: # MERTIC, LOG, ALERT
        return 'customprojectrawdata'


def initialize_api_post_data():
    """ set up the unchanging portion of this """
    to_send_data_dict = dict()
    to_send_data_dict['userName'] = if_config_vars['user_name']
    to_send_data_dict['licenseKey'] = if_config_vars['license_key']
    to_send_data_dict['projectName'] = if_config_vars['project_name']
    to_send_data_dict['instanceName'] = HOSTNAME
    to_send_data_dict['agentType'] = get_agent_type_from_project_type()
    if 'METRIC' in if_config_vars['project_type'] and 'sampling_interval' in if_config_vars:
        to_send_data_dict['samplingInterval'] = str(if_config_vars['sampling_interval'])
    logger.debug(to_send_data_dict)
    return to_send_data_dict


if __name__ == "__main__":
    # declare a few vars
    SPACES = re.compile(r"\s+")
    SLASHES = re.compile(r"\/+")
    UNDERSCORE = re.compile(r"\_+")
    COLONS = re.compile(r"\:+")
    LEFT_BRACE = re.compile(r"\[")
    RIGHT_BRACE = re.compile(r"\]")
    PERIOD = re.compile(r"\.")
    NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")
    PCT_z_FMT = re.compile(r"[\+\-][0-9]{4}")
    PCT_Z_FMT = re.compile(r"[A-Z]{3,4}")
    HOSTNAME = socket.gethostname().partition('.')[0]
    JSON_LEVEL_DELIM = '.'
    CSV_DELIM = ','
    ATTEMPTS = 3
    track = dict()

    # get config
    cli_config_vars = get_cli_config_vars()
    logger = set_logger_config(cli_config_vars['log_level'])
    if_config_vars = get_if_config_vars()
    agent_config_vars = get_agent_config_vars()
    print_summary_info()

    # start data processing
    for i in range(0, cli_config_vars['threads']):
        Process(target=initialize_data_gathering,
                args=(i,)
                ).start()
