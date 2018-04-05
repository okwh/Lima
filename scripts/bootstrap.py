###########################################################################
# This file is part of LImA, a Library for Image Acquisition
#
#  Copyright (C) : 2009-2017
#  European Synchrotron Radiation Facility
#  BP 220, Grenoble 38043
#  FRANCE
# 
#  Contact: lima@esrf.fr
# 
#  This is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 3 of the License, or
#  (at your option) any later version.
# 
#  This software is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
# 
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, see <http://www.gnu.org/licenses/>.
############################################################################
import sys, os
import platform, multiprocessing
from subprocess import Popen, PIPE
import contextlib

OS_TYPE = platform.system()
if OS_TYPE not in ['Linux', 'Windows']:
	sys.exit('Platform not supported: ' + OS_TYPE)

def exec_cmd(cmd, exc_msg=''):
	print('Executing:' + cmd)
	sys.stdout.flush()
	ret = os.system(cmd)
	if ret != 0:
		raise Exception('%s [%s]' % (exc_msg, cmd))


@contextlib.contextmanager
def ch_dir(new_dir):
	cur_dir = os.getcwd()
	os.chdir(new_dir)
	yield
	os.chdir(cur_dir)

class Config:

	defaults = {
		'git': False,
		'find-root-path': '',
		'source-prefix': '',
		'config-file': 'scripts/config.txt',
		'build-prefix': 'build',
		'build-type': ('RelWithDebInfo' if OS_TYPE == 'Linux' 
			       else 'Release'),
		'install': False,
		'install-prefix': '',
		'install-python-prefix': '',
	}

	def __init__(self, argv=None):
		self.cmd_opts = {}
		self.extra_opts = []
		self.config_opts = None
		self.cmake_opts = None
		self.git = None

		if argv is not None:
			self.decode_args(argv)

	def decode_args(self, argv):
		for arg in argv:
			if arg in ['--help', '-help', '-h', '-?']:
				print_help()
			for opt in self.defaults:
				if arg == '--no-%s' % opt:
					arg = '--%s=no' % opt
				prefix = '--%s' % opt
				if arg == prefix:
					self.set_cmd(opt, True)
					break
				prefix += '='
				if arg.startswith(prefix):
					val = arg[len(prefix):]
					val_map = {'yes': True, 'no': False}
					val = val_map.get(val.lower(), val)
					self.set_cmd(opt, val)
					break
			else:
				self.add_extra(arg)

		cwd = os.getcwd()
		if not self.get('source-prefix'):
			self.set_cmd('source-prefix', cwd)
		for opt in ['config-file', 'build-prefix']:
			p = self.get(opt)
			if p and not os.path.isabs(p):
				self.set_cmd(opt, os.path.join(cwd, p))

	def set_cmd(self, x, v):
		self.cmd_opts[x] = v

	def get(self, x, check_defaults=True):
		if x in self.cmd_opts:
			return self.cmd_opts[x]
		return self.defaults[x] if check_defaults else ''

	def add_extra(self, x):
		self.extra_opts.append(x)

	def get_git_options(self):
		return self.extra_opts

	def get_cmd_options(self, check_defaults=False):
		opts = dict(self.defaults) if check_defaults else {}
		opts.update(self.cmd_opts)
		for arg in self.extra_opts:
			for oprefix, sdir in [("limacamera", "camera"), 
					      ("lima-enable", "third-party")]:
				sdir += '/'
				if arg.startswith(sdir):
					arg = oprefix + '-' + arg[len(sdir):]
			opts[arg] = True
		return opts

	def read_config(self):
		config_file = self.get('config-file')
		self.config_opts = []
		with open(config_file) as f:
			for line in f:
				line = line.strip()
				if not line or line.startswith('#'):
					continue
				opt, val = line.split('=')
				opt = self.from_underscore(opt).lower()
				val = int(val) if val.isdigit() else val
				self.config_opts.append((opt, val))

	def get_config_options(self):
		if self.config_opts is None:
			self.read_config()
		return self.config_opts

	def is_install_required(self):
		cmd_opts = self.get_cmd_options()
		install_prefix = cmd_opts.get('install-prefix', '')
		return cmd_opts.get('install', install_prefix != '')

	@staticmethod
	def print_help():
		with open("INSTALL.txt") as f:
			print(f.read())
			sys.exit()

	@staticmethod
	def to_underscore(x):
		return x.replace('-', '_')

	@staticmethod
	def from_underscore(x):
		return x.replace('_', '-')

class CMakeOptions:

	cmd_2_cmake_map = [
		('build-type', 'cmake-build-type'),
		('install-prefix', 'cmake-install-prefix'),
		('install-python-prefix', 'python-site-packages-dir'),
		('find-root-path', 'cmake-find-root-path')
	]

	def __init__(self, cfg):
		self.cfg = cfg


	# return options in config file activated (=1) if passed as arguments,
	# and also those not specified as empty (=) or disabled (=0|no) in file
	def get_configure_options(self):
		cmd_opts = self.cfg.get_cmd_options()
		config_opts = self.cfg.get_config_options()

		def is_active(v):
			if type(val) in [bool, int]:
				return val
			return val and (val.lower() not in [str(0), 'no'])

		cmake_opts = []
		for opt, val in config_opts:
			for cmd_opt, cmd_val in cmd_opts.items():
				if cmd_opt == opt:
					val = cmd_val
					break
				# arg-passed option must match the end
				# of opt a nd must be preceeded by 
				# the '_' separator
				t = opt.split(cmd_opt)
				if ((len(t) == 2) and 
				    (t[0][-1] == '-') and not t[1]):
					val = cmd_val
					break
			if is_active(val):
				cmake_opts.append((opt, val))

		for cmd_key, cmake_key in self.cmd_2_cmake_map:
			val = self.cfg.get(cmd_key)
			if is_active(val) and cmake_key not in dict(cmake_opts):
				cmake_opts.append((cmake_key, val))

		if OS_TYPE == 'Linux':
			cmake_gen = 'Unix Makefiles'
		elif OS_TYPE == 'Windows':
			# for windows check compat between installed python 
			# and mandatory vc++ compiler
			# See, https://wiki.python.org/moin/WindowsCompilers
			if sys.version_info < (2, 6):
				sys.exit("Only python > 2.6 supported")
			elif sys.version_info <= (3, 2):
				win_compiler = "Visual Studio 9 2008"
			elif sys.version_info <= (3, 4):
				win_compiler = "Visual Studio 10 2010" 
			else:
				win_compiler = "Visual Studio 14 2015"
			# now check architecture
			if platform.architecture()[0] == '64bit':
				win_compiler += ' Win64' 

			print ('Found Python ', sys.version)
			print ('Used compiler: ', win_compiler)
			cmake_gen = win_compiler

		source_prefix = self.cfg.get('source-prefix')
		opts = [source_prefix, '-G"%s"' % cmake_gen]
		opts += map(self.cmd_option, cmake_opts)
		return self.get_cmd_line_from_options(opts)

	def get_build_options(self):
		opts = ['--build .']
		if OS_TYPE == 'Linux':
			nb_jobs = multiprocessing.cpu_count() + 1
			opts += ['--', '-j %d' % nb_jobs]
		if OS_TYPE == 'Windows':
			opts += ['--config %s' %  self.cfg.get('build-type')]
		return self.get_cmd_line_from_options(opts)

	def get_install_options(self):
		opts = ['--build .', '--target install'] 
		return self.get_cmd_line_from_options(opts)

	@staticmethod
	def get_cmd_line_from_options(opts):
		return ' '.join(['cmake'] + opts)

	@staticmethod
	def cmd_option(opt_val):
		o, v = opt_val
		def quoted(x):
			if type(x) is bool:
				x = int(x)
			if type(x) is not str:
				x = str(x)
			return (('"%s"' % x) 
				if ' ' in x and not x.startswith('"') else x)
		return '-D%s=%s' % (Config.to_underscore(o).upper(), quoted(v))


class GitHelper:

	not_submodules = (
		'git', 'python', 'tests', 'test', 'cbf', 'lz4', 'fits', 'gz', 
		'tiff', 'hdf5'
	)

	submodule_map = {
		'espia': 'camera/common/espia',
		'pytango-server': 'applications/tango/python',
		'sps-image': 'Sps'
	}

	basic_submods = (
		'Processlib',
	)

	def __init__(self, cfg):
		self.cfg = cfg
		self.opts = self.cfg.get_git_options()

	def check_submodules(self, submodules=None):
		if submodules is None:
			submodules = self.opts
		submodules = list(submodules)
		for submod in self.basic_submods:
			if submod not in submodules:
				submodules.append(submod)

		root = self.cfg.get('source-prefix')
		with ch_dir(root):
			submod_list = []
			for submod in submodules:
				if submod in self.not_submodules:
					continue
				if submod in self.submodule_map:
					submod = self.submodule_map[submod]
				for sdir in ['third-party', 'camera']:
					s = os.path.join(sdir, submod)
					if os.path.isdir(s):
						submod = s
						break
				if os.path.isdir(submod):
					submod_list.append(submod)

			for submod in submod_list:
				self.update_submodule(submod)

	def update_submodule(self, submod):
		try:
			action = 'init ' + submod
			exec_cmd('git submodule ' + action)
			action = 'update ' + submod
			exec_cmd('git submodule ' + action)
			with ch_dir(submod):
				exec_cmd('git submodule init')
				cmd = ['git', 'submodule']
				p = Popen(cmd, stdout=PIPE)
				for l in p.stdout.readlines():
					tok = l.strip().split()
					self.update_submodule(tok[1])
		except Exception as e:
			sys.exit('Problem with submodule %s: %s' % (submod, e))

def build_install_lima(cfg):
	build_prefix = cfg.get('build-prefix')
	if not os.path.exists(build_prefix):
		os.mkdir(build_prefix)
	os.chdir(build_prefix)

	cmake_opts = CMakeOptions(cfg)
	cmake_cmd = cmake_opts.get_configure_options()
	exec_cmd(cmake_cmd, ('Something is wrong in CMake environment. ' +
			     'Make sure your configuration is good.'))

	cmake_cmd = cmake_opts.get_build_options()
	exec_cmd(cmake_cmd, ('CMake could not build Lima. ' + 
			     'Pleae contact lima@esrf.fr for help.'))

	if not cfg.is_install_required():
		return

	cmake_cmd = cmake_opts.get_install_options()
	exec_cmd(cmake_cmd, ('CMake could not install libraries. ' + 
			     'Make sure you have necessary rights.'))


def main():
	cfg = Config(sys.argv[1:])

	# No git option under windows for obvious reasons.
	if OS_TYPE == 'Linux' and cfg.get('git'):
		git = GitHelper(cfg)
		git.check_submodules()

	try:
		build_install_lima(cfg)
	except Exception as e:
		sys.exit('Problem building/installing Lima: %s' % e)



if __name__ == '__main__':
	main()
	
