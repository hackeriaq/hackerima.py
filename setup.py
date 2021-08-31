import os
from setuptools import find_packages, setup

from compliance.linkage import site_settings

with open(os.path.join(os.path.dirname(__file__), '00README')) as readme:
    README = readme.read()

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

# docs build/cleanup
os.system("cd compliance/media/docs && make")

setup(
    name='dep-checker',
    version=site_settings.gui_version,
    packages=find_packages(),
    scripts=['manage.py', 'bin/dep-checker.py', 'bin/readelf.py'],
    install_requires=['Django>=1.8,<1.9'],
    include_package_data=True,
    license='BSD License',  # example license
    description='A framework for finding license dependencies in binary applications.',
    long_description=README,
    url='https://compliance.linuxfoundation.org/',
    author='Jeff Licquia',
    author_email='licquia@linuxfoundation.org',
    classifiers=[
        'Environment :: Web Environment',
        'Framework :: Django',
        'Framework :: Django :: 1.8',  # replace "X.Y" as appropriate
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',  # example license
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        # Replace these appropriately if you are stuck on Python 2.
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Topic :: Internet :: WWW/HTTP',
        'Topic :: Internet :: WWW/HTTP :: Dynamic Content',
    ],
)
