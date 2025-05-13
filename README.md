ShinySDR
========

ShinySDR is the software component of a software-defined radio receiver. When combined with suitable hardware devices such as the RTL-SDR, HackRF, or USRP, it can be used to listen to or display data from a variety of radio transmissions.

* **[More about ShinySDR](https://shinysdr.switchb.org/)**

* **[Installing ShinySDR](https://shinysdr.switchb.org/manual/installation)**


Installing on Arch under virtual env
---------------------
It was tested under Arch Linux with Hackrf.
Python 3.13, GNU Radio 3.10.12
Helpful instruction: https://s-martin.github.io/sdr/shinysdr/raspberrypi/2023/05/21/shinysdr.html

`yay gnuradio`

`python -m venv .`
`source bin/activate`
`pip install attrs setuptools ephem pyasn1 pyasn1-modules pyserial six twisted service_identity pmt ephem txws`

Under virtualenv there is neccessary to have access to gnuradio and osmosdr.
Explanation `https://qoherent.ai/blog/2402-gnu_radio_python_virtual_environment_venv/`
(I also added numpy instead installing from pip).
```
ln -s /usr/lib/python3.13/site-packages/gnuradio/ lib/python3.13/site-packages/gnuradio
ln -s /usr/lib/python3.13/site-packages/numpy/ lib/python3.13/site-packages/numpy
ln -s /usr/lib/python3.13/site-packages/osmosdr/ lib/python3.13/site-packages/osmosdr
```

Building python package
`python3 setup.py build`
`python3 setup.py install`

Then create config:
`shinysdr --create ./shinysdr-config`
I modified config.py from created dir.
`config.devices.add(u'osmo', OsmoSDRDevice('hackrf=0'))`
`root_cap=None`

Unfortunately txws does not work after installing using pip.
After installation txws.py must be modified as in this pull request(lib/python3.13/site-packages/txws.py):
`https://github.com/MostAwesomeDude/txWS/pull/34/files`

Now shinysdr can be started.
`shinysdr shinysdr-config/`


Copyright and License
---------------------

Copyright 2013, 2014, 2015, 2016, 2017, 2018, 2019 Kevin Reid &lt;kpreid@switchb.org&gt;

ShinySDR is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

ShinySDR is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with ShinySDR.  If not, see <http://www.gnu.org/licenses/>.

### Additional information

* The file `shinysdr/i/webstatic/client/map/basemap.geojson.gz` was derived from [the Natural Earth data set `ne_50m_admin_0_countries`, version 2.0.0](http://www.naturalearthdata.com/downloads/50m-cultural-vectors/).
    This data set [is in the public domain](http://www.naturalearthdata.com/about/terms-of-use/).
* The APRS symbol graphics and descriptions used are from various sources and [collected by Heikki Hannikainen](https://github.com/hessu/aprs-symbols).
    See [author credits and licensing information](https://github.com/hessu/aprs-symbols/blob/master/COPYRIGHT.md).
