#!/usr/bin/env python
#
# Internet Systems Consortium license
#
# Copyright (c) 2014, Franck Cuny (<franckcuny@gmail.com>)
#
# Permission to use, copy, modify, and/or distribute this software for any purpose
# with or without fee is hereby granted, provided that the above copyright notice
# and this permission notice appear in all copies.

# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
# REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
# FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
# INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
# OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
# TORTUOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
# THIS SOFTWARE.

# TODO -- proper exception handling and meaningful error codes


'''
Foreman external inventory script
=================================

Generates inventory that Ansible can understand by making API requests
to Foreman.

Information about the Foreman's instance can be stored in the ``foreman.ini`` file.
A ``base_url``, ``username`` and ``password`` need to be provided. The path to an
alternate configuration file can be provided by exporting the ``FOREMAN_INI_PATH``
variable.

When run against a specific host, this script returns the following variables
based on the data obtained from Foreman:
 - id
 - ip
 - name
 - environment
 - os
 - model
 - compute_resource
 - domain
 - architecture
 - created
 - updated
 - status
 - hostgroup
 - ansible_ssh_host

When run in --list mode, instances are grouped by the following categories:
 - group

Examples:
  Execute uname on all instances in the dev group
  $ ansible -i theforeman.py dev -m shell -a \"/bin/uname -a\"

Authors:  Franck Cuny <franckcuny@gmail.com>
          Andrew Deck <andrew.deck@outlook.com>
Version: 0.0.2
'''

import sys
import os
import re
import optparse
import ConfigParser
import collections

try:
    import json
except ImportError:
    import simplejson as json

try:
    from foreman.client import Foreman
    from requests.exceptions import ConnectionError
except ImportError, e:
    print ('python-foreman required for this module')
    print e
    sys.exit(1)

class CLIMain(object):
  def __init__(self):
    """Program entry point"""
    settings = self.read_settings()
    self.parse_cli_args()
    foreman = ForemanInventory(settings['username']
                              ,settings['password']
                              ,settings['base_url'])
    if self.args.all:
      data_to_print = foreman.get_all()
    elif self.args.host:
      data_to_print = foreman.get_host_info(self.args.host)
    elif self.args.list:
      data_to_print = foreman.get_inventory()
    else:
      data_to_print = {}
    print(json.dumps(data_to_print, sort_keys=True, indent=4))
    
  def read_settings(self):
    """Read the settings from the foreman.ini file"""
    config = ConfigParser.SafeConfigParser()
    scriptdir = os.path.dirname(os.path.realpath(__file__))
    foreman_default_ini_path = os.path.join(scriptdir, 'foreman.ini')
    foreman_ini_path = os.environ.get('FOREMAN_INI_PATH', foreman_default_ini_path)
    config.read(foreman_ini_path)
    settings = {}
    required = ['base_url', 'username', 'password']
    missing = []
    for setting in required:
      val = config.get('foreman', setting)
      if val is None:
        missing.append(setting)
      settings[setting] = val
    if missing != []:
      raise Exception("Could not find values for Foreman " + ', '.join(missing)
                      + ". They must be specified via ini file.")
    return settings

  def parse_cli_args(self):
    """Command line argument processing"""
    description = 'Produce an Ansible Inventory file based on Foreman'
    parser = optparse.OptionParser(description=description)
    parser.add_option('--list', action='store_true', default=True,
          help='List instances (default: True)')
    parser.add_option('--host', action='store',
          help='Get all the variables about a specific instance')
    parser.add_option('--all', action='store_true',
          help= 'Get all the variables for all the instances')
    (self.args, self.options) = parser.parse_args()

class ForemanInventory(object):
    """Foreman Inventory"""

    def _empty_inventory(self):
        """Empty inventory"""
        return {'_meta': {'hostvars': {}}}

    def _empty_cache(self):
        """Empty cache"""
        keys = ['operatingsystem', 'hostgroup', 'environment', 'model',
                'compute_resource', 'domain', 'subnet', 'architecture',
                'host']
        keys_d = {}
        for i in keys:
          keys_d[i] = {}
        return keys_d

    def __init__(self, username, password, foreman_url):
        # initializing vars
        self.base_url = foreman_url
        self.username = username
        self.password = password
        self.inventory = self._empty_inventory()
        self._cache = self._empty_cache()
        try:
          self.client = Foreman(self.base_url, (self.username, self.password))
        except ConnectionError, e:
          raise Exception("It looks like Foreman's API is unreachable. Error "
                          "was: " + str(e))

    def get_host_info(self, host_id):
        """Get information about an host"""
        host_desc = {}

        meta = self._get_object_from_id('host', host_id)
        if meta is None:
            return host_desc
        for k in ['id', 'ip', 'name', 'status']:
          host_desc[k] = meta.get(k)
        for k in ['model', 'compute_resource', 'domain', 'subnet'
                  ,'architecture', 'hostgroup']:
          host_desc[k] = self._get_from_type(k, meta)
        for k in ['created', 'updated']:
          host_desc[k] = meta.get(k + '_at')
        host_desc['os'] = self._get_from_type('operatingsystem', meta)
        try:
          k = 'environment'
          host_desc[k] = meta.get(k).get(k).get('name').lower()
        except Exception:
          pass # do nothing
        # to ssh from ansible
        host_desc['ansible_ssh_host'] = host_desc['ip']
        return host_desc

    def get_inventory(self):
        """Get all the hosts from the inventory"""
        groups = collections.defaultdict(list)
        hosts  = []
        page = 1
        while True:
            resp = self.client.index_hosts(page=page)
            if len(resp) < 1:
                break
            page  += 1
            hosts += resp
        if len(hosts) < 1:
            return groups
        for host in hosts:
            host_group = self._get_from_id('hostgroup', host.get('host').get('hostgroup_id'))
            server_name = host.get('host').get('name')
            groups[host_group].append(server_name)
        return groups

    def get_all(self):
      """Get all the machines and all the variables for all the machines"""
      groups = self.get_inventory()
      hosts = {}
      for group in groups:
        for host in groups[group]:
          hosts[host] = True
      for host in hosts:
        hosts[host] = self.get_host_info(host)
      groups['_meta'] = {'hostvars': hosts}
      return groups

    def _get_from_type(self, param_type, host):
      return self._get_from_id(param_type, host.get(param_type + '_id'))

    def _get_from_id(self, param_type, param_id):
        """Get the object of type param_type associated with the ID param_id
        The following values for param_type are explicitly accounted for:
        - architecture
        - subnet
        - domain
        - compute_resource
        - model
        - environment
        - label
        - hostgroup
        - operatingsystem
        """
        param = self._get_object_from_id(param_type, param_id)
        if param is None:
            return None
        if param_type == "hostgroup":
            return param.get('label')
        elif param_type == 'operatingsystem':
            return "{0}-{1}".format(param.get('name'), param.get('major'))
        elif param_type == 'environment':
            return param.get('name').lower()
        else:
            return param.get('name')

    def _get_object_from_id(self, obj_type, obj_id):
        """Get an object from it's ID"""
        if obj_id is None:
            return None
        obj = self._cache.get(obj_type).get(obj_id, None)
        if obj is None:
            method_name = "show_{0}s".format(obj_type)
            func = getattr(self.client, method_name)
            obj = func(obj_id)
            self._cache[obj_type][obj_id] = obj
        return obj.get(obj_type)

if __name__ == '__main__':
  CLIMain()

