# Copyright 2013, 2014, 2015, 2016, 2017, 2018, 2019 Kevin Reid and the ShinySDR contributors
#
# This file is part of ShinySDR.
# 
# ShinySDR is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# ShinySDR is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with ShinySDR.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import, division, print_function, unicode_literals

from math import pi

import six

from zope.interface import implementer

from gnuradio import gr
from gnuradio import blocks
from gnuradio import analog
from gnuradio.filter import window
from gnuradio import filter as grfilter  # don't shadow builtin
from gnuradio.analog import fm_emph
from gnuradio.filter import firdes

from shinysdr.interfaces import BandShape, ModeDef, IDemodulator, IModulator, ITunableDemodulator
from shinysdr.math import dB, to_dB
from shinysdr.filters import MultistageChannelFilter, make_resampler, design_sawtooth_filter
from shinysdr.i.pycompat import defaultstr
from shinysdr.signals import SignalType
from shinysdr.types import EnumT, EnumRow, RangeT
from shinysdr import units
from shinysdr.values import ExportedState, exported_value, setter


TWO_PI = pi * 2

BASIC_MODE_SORT_PREFIX = ' '


class SquelchMixin(ExportedState):
    """Provides simple RF-power squelch and a level meter.
    
    To use, connect self.squelch_block in the pre-demodulation signal path.
    """
    
    def __init__(self, squelch_rate, squelch_threshold=-100):
        alpha = 80.0 / squelch_rate
        
        self.__squelch = analog.simple_squelch_cc(squelch_threshold, alpha)
        self.__probe = analog.probe_avg_mag_sqrd_c(0, alpha=alpha)
        
        self.squelch_block = gr.hier_block2(
            defaultstr('SquelchMixin bundle'),
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.squelch_block.connect(
            self.squelch_block,
            self.__squelch,
            self.squelch_block)
        self.squelch_block.connect(
            self.squelch_block,
            self.__probe)
    
    @exported_value(
        type=RangeT([(-100, 0)], unit=units.dBFS, strict=False),
        changes='continuous',
        label='Channel power')
    def get_rf_power(self):
        return to_dB(max(1e-10, self.__probe.level()))

    @exported_value(
        type=RangeT([(-100, 0)], unit=units.dBFS, strict=False, logarithmic=False),
        changes='this_setter',
        label='Squelch')
    def get_squelch_threshold(self):
        return self.__squelch.threshold()

    @setter
    def set_squelch_threshold(self, level):
        self.__squelch.set_threshold(level)


@implementer(ITunableDemodulator)
class ChannelFilterMixin(object):
    """Provides a MultistageChannelFilter block and matching implementations of get_band_shape and ITunableDemodulator.
    
    Does not make any connection automatically.
    """
    
    def __init__(self, input_rate=0, demod_rate=0, cutoff_freq=0, transition_width=0):
        # mandatory keyword arguments
        assert input_rate > 0
        assert demod_rate > 0
        assert cutoff_freq > 0
        assert transition_width > 0
        
        self.channel_filter_block = MultistageChannelFilter(
            input_rate=input_rate,
            output_rate=demod_rate,
            cutoff_freq=cutoff_freq,
            transition_width=transition_width)
    
    @exported_value(type=BandShape, changes='never')
    def get_band_shape(self):
        """Implements IDemodulator."""
        return self.channel_filter_block.get_shape()

    def set_rec_freq(self, freq):
        """Implements ITunableDemodulator."""
        self.channel_filter_block.set_center_freq(freq)


@implementer(IDemodulator)
class SimpleAudioDemodulator(ChannelFilterMixin, gr.hier_block2, ExportedState):
    def __init__(self, 
            input_rate,
            # parameters provided by subclasses
            demod_rate, audio_rate, band_filter, band_filter_transition, stereo=False,
            # standard demodulator parameters which we want to allow usage without
            mode=None, context=None):
        """All arguments should be passed as keywords."""
        
        self.__channels = channels = 2 if stereo else 1
        self.__signal_type = SignalType(
            kind='STEREO' if stereo else 'MONO',
            sample_rate=audio_rate)
        
        # TODO: Review which of these attributes are actually used
        self.mode = mode
        self.context = context
        self.input_rate = input_rate
        self.demod_rate = demod_rate
        self.audio_rate = audio_rate
        
        gr.hier_block2.__init__(
            self, defaultstr(u'%s(mode=%r)' % (type(self).__name__, mode)),
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(1, 1, gr.sizeof_float * channels))
        ChannelFilterMixin.__init__(self,
            input_rate=self.input_rate,
            demod_rate=demod_rate,
            cutoff_freq=band_filter,
            transition_width=band_filter_transition)
    
    def get_output_type(self):
        """Implements IDemodulator."""
        return self.__signal_type

    def connect_audio_output(self, l_endpoint, r_endpoint=None):
        # TODO: Convert this into a separate helper function?
        stereo = r_endpoint is not None
        assert stereo == (self.__channels == 2)
        if stereo:
            joiner = blocks.streams_to_vector(gr.sizeof_float, 2)
            self.connect(l_endpoint, (joiner, 0), self)
            self.connect(r_endpoint, (joiner, 1))
        else:
            self.connect(l_endpoint, self)


def design_lofi_audio_filter(rate, lowpass):
    """
    Audio output filter for speech-type receivers.
    
    Original motivation was to remove CTCSS tones.
    """
    upper = min(10000, rate / 2)
    transition = 1000
    if lowpass:
        return firdes.low_pass(
            1.0,
            rate,
            upper,
            transition,
            window.WIN_HAMMING)
    else:
        return firdes.band_pass(
            1.0,
            rate,
            500,
            upper,
            transition,
            window.WIN_HAMMING)


class IQDemodulator(SquelchMixin, SimpleAudioDemodulator):
    # TODO: Allow a choice of bandwidth/sample rate
    def __init__(self, mode='IQ', **kwargs):
        audio_rate = 96000
        SimpleAudioDemodulator.__init__(self,
            mode=mode,
            stereo=True,
            audio_rate=audio_rate,
            demod_rate=audio_rate,
            band_filter=audio_rate * 0.5,
            band_filter_transition=audio_rate * 0.2,
            **kwargs)
        SquelchMixin.__init__(self, audio_rate)
        
        self.split_block = blocks.complex_to_float(1)
        
        self.connect(
            self,
            self.channel_filter_block,
            self.squelch_block,
            self)


pluginDef_iq = ModeDef(mode='IQ',
    info='Raw I/Q',
    demod_class=IQDemodulator)


_am_lower_cutoff_freq = 40
_am_audio_bandwidth = 7500
_am_demod_method_type = EnumT({
    u'async': u'Asynchronous',
    u'lsb': u'Lower sideband',
    u'usb': u'Upper sideband',
    u'stereo': u'ISB stereo',
})


class AMDemodulator(SquelchMixin, SimpleAudioDemodulator):
    """Amplitude modulation (AM) demodulator."""
    
    __demod_rate = 16000
    
    def __init__(self, context, demod_method=u'async', **kwargs):
        SimpleAudioDemodulator.__init__(self,
            context=context,
            stereo=True,
            audio_rate=self.__demod_rate,
            demod_rate=self.__demod_rate,
            band_filter=_am_audio_bandwidth,
            band_filter_transition=1000,
            **kwargs)
        SquelchMixin.__init__(self, self.__demod_rate)
        
        self.__context = context
        
        self.__demod_method = _am_demod_method_type(demod_method)
        self.__pll = None
        
        self.__do_connect()
    
    @exported_value(
        type=_am_demod_method_type,
        parameter='demod_method',
        changes='this_setter',
        label='AM demodulation')
    def get_demod_method(self):
        return self.__demod_method
    
    @setter
    def set_demod_method(self, value):
        value = _am_demod_method_type(value)
        if value == self.__demod_method:
            return
        self.__demod_method = value
        self.__context.lock()
        self.__do_connect()
        self.__context.unlock()
    
    def __do_connect(self):
        inherent_gain = 0.5  # fudge factor so that our output is similar level to narrow FM
        if self.__demod_method != 'async':
            inherent_gain *= 2
        
        agc_block = analog.feedforward_agc_cc(int(.005 * self.__demod_rate), inherent_gain)
        
        # non-method-specific elements
        self.disconnect_all()
        self.connect(
            self,
            self.channel_filter_block,  # from ChannelFilterMixin
            self.squelch_block,  # from SquelchMixin
            agc_block)
        before_demod = agc_block
        
        if self.__demod_method == u'async':
            dc_blocker = self.__make_dc_blocker()
            self.connect(
                before_demod,
                blocks.complex_to_mag(1),
                dc_blocker)
            self.connect_audio_output(dc_blocker, dc_blocker)
            self.__pll = None
        else:
            # all other methods use carrier tracking
            # TODO: refine PLL parameters further
            pll = self.__pll = analog.pll_carriertracking_cc(.01 * pi, .1 * pi, -.1 * pi)
            pll.set_lock_threshold(dB(-20))
            # pll.squelch_enable(True)
            self.connect(before_demod, pll)
            
            if self.__demod_method == u'stereo':
                left_input, left_output = self.__make_sideband_demod(False)
                right_input, right_output = self.__make_sideband_demod(True)
                self.connect(pll, left_input)
                self.connect(pll, right_input)
                self.connect_audio_output(left_output, right_output)
            else:
                (demod_input, demod_output) = self.__make_sideband_demod(self.__demod_method == u'usb')
                self.connect(pll, demod_input)
                self.connect_audio_output(demod_output, demod_output)
    
    def __make_sideband_demod(self, upper):
        first = grfilter.fir_filter_ccc(
            1,
            firdes.complex_band_pass(1.0, self.__demod_rate,
                _am_lower_cutoff_freq if upper else -_am_audio_bandwidth,
                _am_audio_bandwidth if upper else -_am_lower_cutoff_freq,
                1000,
                window.WIN_HAMMING))
        last = self.__make_dc_blocker()
        self.connect(first, blocks.complex_to_real(), last)
        return first, last
    
    def __make_dc_blocker(self):
        # We use the DC blocker even when we also have a band pass filter because it is (TODO verify) cheaper than a sharp filter.
        return grfilter.dc_blocker_ff(self.__demod_rate // _am_lower_cutoff_freq, False)
    
    # this needs UI cleanup before we want to expose it
    # @exported_value(type=QuantityT(units.Hz))
    # def get_pll_frequency(self):
    #     if self.__pll:
    #         return self.__pll.get_frequency() * (self.input_rate / TWO_PI) + self.context.get_absolute_frequency_cell().get()
    #     else:
    #         return 0
    
    # disabled because I haven't found any combination of parameters which makes the lock detector reliably useful
    # @exported_value(type=NoticeT())
    # def get_pll_locked(self):
    #     if self.__pll and not self.__pll.lock_detector():
    #         return u'No carrier!'
    #     else:
    #         return u''


@implementer(IDemodulator, ITunableDemodulator)
class UnselectiveAMDemodulator(gr.hier_block2, ExportedState):
    """
    Wideband AM demodulator. Ignores the receive frequency and demodulates the entire RF signal.
    """
    
    def __init__(self, mode, input_rate, context):
        channels = 2
        audio_rate = 10000
        
        gr.hier_block2.__init__(
            self, str('%s demodulator' % (mode,)),
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(1, 1, gr.sizeof_float * channels))

        self.__input_rate = input_rate
        self.__rec_freq_input = 0.0
        self.__signal_type = SignalType(kind='STEREO', sample_rate=audio_rate)

        # Using agc2 rather than feedforward AGC for efficiency, because this runs at the RF rate rather than the audio rate.
        agc_block = analog.agc2_cc(reference=dB(-8))
        agc_block.set_attack_rate(8e-3)
        agc_block.set_decay_rate(8e-3)
        agc_block.set_max_gain(dB(40))
        
        self.connect(
            self,
            agc_block)
        
        channel_joiner = blocks.streams_to_vector(gr.sizeof_float, channels)
        self.connect(channel_joiner, self)
        
        for channel in six.moves.range(0, channels):
            self.connect(
                agc_block,
                grfilter.fir_filter_ccc(1, design_sawtooth_filter(decreasing=channel == 0)),
                blocks.complex_to_mag(1),
                blocks.float_to_complex(),  # So we can use the complex-input band filter. TODO eliminate this for efficiency
                MultistageChannelFilter(
                    input_rate=input_rate,
                    output_rate=audio_rate,
                    cutoff_freq=5000,
                    transition_width=5000),
                blocks.complex_to_real(),
                # assuming below 40Hz is not of interest
                grfilter.dc_blocker_ff(audio_rate // 40, False),
                (channel_joiner, channel))
    
    @exported_value(type=BandShape, changes='explicit')
    def get_band_shape(self):
        """implement IDemodulator"""
        halfbw = self.__input_rate * 0.5
        offset = self.__rec_freq_input
        epsilon = 1  # don't be invalid in case of floating-point error
        return BandShape(
            stop_low=-halfbw - offset,
            stop_high=halfbw - offset,
            pass_low=-halfbw - offset + epsilon,
            pass_high=-halfbw - offset + epsilon,
            markers={})
    
    def get_output_type(self):
        """implement IDemodulator"""
        return self.__signal_type

    def set_rec_freq(self, freq):
        """implement ITunableDemodulator"""
        # By implementing ITunableDemodulator and doing nothing, we use the hardware frequency without changes.
        self.__rec_freq_input = freq
        self.state_changed('band_shape')


@implementer(IModulator)
class AMModulator(gr.hier_block2, ExportedState):
    def __init__(self, context, mode, rate=10000):
        gr.hier_block2.__init__(
            self, type(self).__name__,
            gr.io_signature(1, 1, gr.sizeof_float * 1),
            gr.io_signature(1, 1, gr.sizeof_gr_complex * 1),
        )
        
        self.__rate = rate
        
        self.connect(
            self,
            blocks.float_to_complex(1),
            blocks.add_const_cc(1),
            self)
    
    def get_input_type(self):
        return SignalType(kind='MONO', sample_rate=self.__rate)
    
    def get_output_type(self):
        return SignalType(kind='IQ', sample_rate=self.__rate)


pluginDef_am = ModeDef(mode='AM',
    info=EnumRow(label='AM', sort_key=BASIC_MODE_SORT_PREFIX + 'AM'),
    demod_class=AMDemodulator,
    mod_class=AMModulator)
pluginDef_am_entire = ModeDef(mode='AM-unsel',
    info=EnumRow(label='AM unselective', sort_key=BASIC_MODE_SORT_PREFIX + 'AM unsel'),
    demod_class=UnselectiveAMDemodulator)


class FMDemodulator(SquelchMixin, SimpleAudioDemodulator):
    def __init__(self,
            mode,
            deviation=75000,
            demod_rate=48000,
            band_filter=None,
            band_filter_transition=None,
            tau=75e-6,
            no_audio_filter=False,  # TODO kludge to support APRS demod looking for tones
            **kwargs):
        SimpleAudioDemodulator.__init__(self,
            mode=mode,
            demod_rate=demod_rate,
            band_filter=band_filter,
            band_filter_transition=band_filter_transition,
            **kwargs)
        SquelchMixin.__init__(self, self.demod_rate)
        
        self.__no_audio_filter = no_audio_filter
        
        self.__qdemod = analog.quadrature_demod_cf(demod_rate / (TWO_PI * deviation))
        if tau is not None and tau > 0.0:
            self.__deemph = fm_emph.fm_deemph(demod_rate, tau)
        else:
            self.__deemph = None
        
        self.do_connect()
    
    def do_connect(self):
        self.disconnect_all()
        self.connect(
            self,
            self.channel_filter_block,
            self.squelch_block,
            self.__qdemod)
        if self.__deemph is not None:
            self.connect(self.__qdemod, self.__deemph)
            output = self.__deemph
        else:
            output = self.__qdemod
        self.connect_audio_stage(output)
    
    def _make_resampler(self, input_port, input_rate):
        taps = design_lofi_audio_filter(input_rate, self.__no_audio_filter)
        if self.audio_rate == input_rate:
            filt = grfilter.fir_filter_fff(1, taps)
            self.connect(input_port, filt)
            return filt
        elif input_rate % self.audio_rate == 0:
            filt = grfilter.fir_filter_fff(input_rate // self.audio_rate, taps)
            self.connect(input_port, filt)
            return filt
        else:
            # TODO: use combined filter and resampler (need to move filter design)
            filt = grfilter.fir_filter_fff(1, taps)
            resampler = make_resampler(input_rate, self.audio_rate)
            self.connect(input_port, filt, resampler)
            return resampler

    def connect_audio_stage(self, input_port):
        """Override point for stereo"""
        resampler = self._make_resampler(input_port, self.demod_rate)
        self.connect_audio_output(resampler)


class NFMDemodulator(FMDemodulator):
    def __init__(self, **kwargs):
        # TODO support 2.5kHz deviation
        audio_rate = 10000  # TODO justify
        deviation = 5000
        transition = 1000
        FMDemodulator.__init__(self,
            demod_rate=max(deviation * 3, audio_rate),  # TODO justify the 3
            audio_rate=audio_rate,
            deviation=deviation,
            band_filter=deviation + transition * 0.5,
            band_filter_transition=transition,
            **kwargs)


@implementer(IModulator)
class NFMModulator(gr.hier_block2, ExportedState):
    def __init__(self, context, mode, audio_rate=10000, rf_rate=20000):
        gr.hier_block2.__init__(
            self, type(self).__name__,
            gr.io_signature(1, 1, gr.sizeof_float * 1),
            gr.io_signature(1, 1, gr.sizeof_gr_complex * 1),
        )
        
        self.__audio_rate = audio_rate
        self.__rf_rate = rf_rate
        
        self.connect(
            self,
            analog.nbfm_tx(
                audio_rate=audio_rate,
                quad_rate=rf_rate,
                tau=75e-6,
                max_dev=5e3),
            self)
    
    def get_input_type(self):
        return SignalType(kind='MONO', sample_rate=self.__audio_rate)
    
    def get_output_type(self):
        return SignalType(kind='IQ', sample_rate=self.__rf_rate)


pluginDef_nfm = ModeDef(mode='NFM',  # TODO also declare 'FM' mode, and be consistent about narrowbanding
    info=EnumRow(label='Narrow FM',
        description='FM with 5 kHz deviation',
        sort_key=BASIC_MODE_SORT_PREFIX + 'FM'),
    demod_class=NFMDemodulator,
    mod_class=NFMModulator)


class WFMDemodulator(FMDemodulator):
    def __init__(self, decode_stereo=True, **kwargs):
        self.__decode_stereo = decode_stereo
        self.__audio_int_rate = 40000  # lower than demod rate, higher than audio filter
        
        FMDemodulator.__init__(self,
            stereo=True,  # config for stereo because we can't change at runtime
            audio_rate=self.__audio_int_rate,
            demod_rate=200000,  # higher than deviation*2, higher than stereo pilot freq, multiple of __audio_int_rate
            deviation=75000,
            band_filter=80000,
            band_filter_transition=20000,
            tau=None,  # disable deemphasis because it is too early
            no_audio_filter=True,  # disable highpass
            **kwargs)

    @exported_value(type=bool, changes='this_setter', parameter='decode_stereo', label='Stereo')
    def get_decode_stereo(self):
        return self.__decode_stereo
    
    @setter
    def set_decode_stereo(self, value):
        value = bool(value)
        if value == self.__decode_stereo:
            return
        self.__decode_stereo = value
        self.context.rebuild_me()
    
    def connect_audio_stage(self, input_port):
        stereo_rate = self.demod_rate
        normalizer = TWO_PI / stereo_rate
        pilot_tone = 19000
        pilot_low = pilot_tone * 0.98
        pilot_high = pilot_tone * 1.02

        def make_audio_filter():
            return grfilter.fir_filter_fff(
                stereo_rate // self.__audio_int_rate,  # decimation
                firdes.low_pass(
                    1.0,
                    stereo_rate,
                    15000,
                    5000,
                    window.WIN_HAMMING))

        stereo_pilot_filter = grfilter.fir_filter_fcc(
            1,  # decimation
            firdes.complex_band_pass(
                1.0,
                stereo_rate,
                pilot_low,
                pilot_high,
                300))  # TODO magic number from gqrx
        stereo_pilot_pll = analog.pll_refout_cc(
            loop_bw=0.001,
            max_freq=normalizer * pilot_high,
            min_freq=normalizer * pilot_low)
        stereo_pilot_doubler = blocks.multiply_cc()
        stereo_pilot_out = blocks.complex_to_real()
        difference_channel_mixer = blocks.multiply_ff()
        difference_channel_filter = make_audio_filter()
        mono_channel_filter = make_audio_filter()
        mixL = blocks.add_ff(1)
        mixR = blocks.sub_ff(1)
        
        # connections
        self.connect(input_port, mono_channel_filter)
        if self.__decode_stereo:
            # stereo pilot tone tracker
            self.connect(
                input_port,
                stereo_pilot_filter,
                stereo_pilot_pll)
            self.connect(stereo_pilot_pll, (stereo_pilot_doubler, 0))
            self.connect(stereo_pilot_pll, (stereo_pilot_doubler, 1))
            self.connect(stereo_pilot_doubler, stereo_pilot_out)
        
            # pick out stereo left-right difference channel (at stereo_rate)
            self.connect(input_port, (difference_channel_mixer, 0))
            self.connect(stereo_pilot_out, (difference_channel_mixer, 1))
            self.connect(
                difference_channel_mixer,
                blocks.multiply_const_ff(6),  # TODO: Completely empirical fudge factor. This should not be necessary. I believe this is at least partly due to phase error in the pilot signal.
                difference_channel_filter)
        
            # recover left/right channels (at self.__audio_int_rate)
            self.connect(difference_channel_filter, (mixL, 1))
            self.connect(difference_channel_filter, (mixR, 1))
            self.connect(mono_channel_filter, (mixL, 0))
            self.connect(mono_channel_filter, (mixR, 0))
            resamplerL = self._make_resampler((mixL, 0), self.__audio_int_rate)
            resamplerR = self._make_resampler((mixR, 0), self.__audio_int_rate)
            deemphL = fm_emph.fm_deemph(self.__audio_int_rate, 75e-6)
            deemphR = fm_emph.fm_deemph(self.__audio_int_rate, 75e-6)
            self.connect(resamplerL, deemphL)
            self.connect(resamplerR, deemphR)
            self.connect_audio_output(deemphL, deemphR)
        else:
            resampler = self._make_resampler(mono_channel_filter, self.__audio_int_rate)
            deemph = fm_emph.fm_deemph(self.__audio_int_rate, 75e-6)
            self.connect(resampler, deemph)
            self.connect_audio_output(deemph, deemph)


pluginDef_wfm = ModeDef(mode='WFM',
    info=EnumRow(label='Broadcast FM',
        description='FM with 75 kHz deviation and stereo subcarrier',
        sort_key=BASIC_MODE_SORT_PREFIX + 'FM W'),
    demod_class=WFMDemodulator)


_ssb_max_agc = 40


class SSBDemodulator(SimpleAudioDemodulator):
    # TODO: implement IDemodulatorModeChange
    
    def __init__(self, mode, **kwargs):
        if mode == 'LSB':
            lsb = True
            cw = False
        elif mode == 'USB':
            lsb = False
            cw = False
        elif mode == 'CW':
            lsb = False
            cw = True
        else:
            raise ValueError('Not an SSB mode: %r' % (mode,))
        
        demod_rate = 8000  # round number close to SSB bandwidth * 2
        
        SimpleAudioDemodulator.__init__(self,
            mode=mode,
            audio_rate=demod_rate,
            demod_rate=demod_rate,
            band_filter=demod_rate / 2,  # note narrower filter applied later
            band_filter_transition=demod_rate / 2,
            **kwargs)
        
        if cw:
            self.__offset = 1500  # CW beat frequency
            half_bandwidth = self.half_bandwidth = 500
            band_filter_width = 120
            band_mid = 0
            agc_reference = dB(-10)
            self.__agc_rate = 1e-1
        else:
            self.__offset = 0
            half_bandwidth = self.half_bandwidth = 2800 / 2  # standard SSB bandwidth
            band_filter_width = half_bandwidth / 5
            if lsb:
                band_mid = -200 - half_bandwidth
            else:
                band_mid = 200 + half_bandwidth
            agc_reference = dB(-8)
            self.__agc_rate = 8e-1
        
        band_filter_low = band_mid - half_bandwidth
        band_filter_high = band_mid + half_bandwidth
        sharp_filter_block = grfilter.fir_filter_ccc(
            1,
            firdes.complex_band_pass(1.0, demod_rate,
                band_filter_low + self.__offset,
                band_filter_high + self.__offset,
                band_filter_width,
                window.WIN_HAMMING))
        self.__filter_shape = BandShape.bandpass_transition(
            low=band_filter_low,
            high=band_filter_high,
            transition=band_filter_width,
            markers={})
        
        self.agc_block = analog.agc2_cc(reference=agc_reference)
        self.agc_block.set_attack_rate(self.__agc_rate)
        self.agc_block.set_decay_rate(self.__agc_rate)
        self.agc_block.set_max_gain(dB(_ssb_max_agc))
        
        ssb_demod_block = blocks.complex_to_real(1)
        
        self.connect(
            self,
            self.channel_filter_block,
            sharp_filter_block,
            self.agc_block,
            ssb_demod_block)
        self.connect_audio_output(ssb_demod_block)
    
    # override
    @exported_value(type=BandShape, changes='never')
    def get_band_shape(self):
        return self.__filter_shape
    
    # override
    def set_rec_freq(self, freq):
        super(SSBDemodulator, self).set_rec_freq(freq - self.__offset)
    
    @exported_value(
        type=RangeT([(-20, _ssb_max_agc)], unit=units.dB),
        changes='continuous',
        label='AGC')
    def get_agc_gain(self):
        return to_dB(self.agc_block.gain())
    
    @exported_value(
        type=bool,
        # If this is persisted then the AGC will be frozen at the unrelated default gain of 1.0 in any newly created demodulator. We would need to persist the gain value too, but persisting changes='continuous' isn't supported so we'd need to implement notifications.
        persists=False,
        changes='this_setter',
        label='AGC Enabled')
    def get_agc_enabled(self):
        return self.agc_block.decay_rate() > 0
    
    @setter
    def set_agc_enabled(self, value):
        agc_rate = self.__agc_rate if value else 0.
        self.agc_block.set_decay_rate(agc_rate)
        self.agc_block.set_attack_rate(agc_rate)


@implementer(IModulator)
class DSBModulator(gr.hier_block2, ExportedState):
    def __init__(self, context, mode, rate=8000):
        gr.hier_block2.__init__(
            self, type(self).__name__,
            gr.io_signature(1, 1, gr.sizeof_float * 1),
            gr.io_signature(1, 1, gr.sizeof_gr_complex * 1),
        )
        
        self.__rate = rate
        
        self.connect(
            self,
            blocks.float_to_complex(1),
            self)
    
    def get_input_type(self):
        return SignalType(kind='MONO', sample_rate=self.__rate)
    
    def get_output_type(self):
        return SignalType(kind='IQ', sample_rate=self.__rate)


# TODO: implement SSB, not DSB, modulator
pluginDef_lsb = ModeDef(mode='LSB',
    info=EnumRow(
        label='LSB',
        description='Single-sideband, lower sideband',
        sort_key=BASIC_MODE_SORT_PREFIX + 'SSB L'),
    demod_class=SSBDemodulator,
    mod_class=DSBModulator)
pluginDef_usb = ModeDef(mode='USB',
    info=EnumRow(
        label='USB',
        description='Single-sideband, upper sideband',
        sort_key=BASIC_MODE_SORT_PREFIX + 'SSB U'),
    demod_class=SSBDemodulator,
    mod_class=DSBModulator)
pluginDef_cw = ModeDef(mode='CW',
    info=EnumRow(
        label='CW',
        sort_key=BASIC_MODE_SORT_PREFIX + 'CW'),
    demod_class=SSBDemodulator,
    mod_class=DSBModulator)
