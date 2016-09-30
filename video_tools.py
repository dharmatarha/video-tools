# -*- coding: utf-8 -*-
"""
Created on Thu Oct  7 15:32:44 2014

@author: Hjalmar K. Turesson
"""
import os
import numpy as np
from datetime import datetime
import time
import pathlib
# Setting GST_DEBUG_DUMP_DOT_DIR environment variable enables us to  have a
#  dotfile generated. The environment variable cannot be set inside the class.
os.environ["GST_DEBUG_DUMP_DOT_DIR"] = "/tmp"
# GStreamer imports
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')
#gi.require_version('Gtk', '3.0')
from gi.repository import GObject, Gst
Gst.init(None)
Gst.debug_set_active(False)
Gst.debug_set_default_threshold(0)
from gi.repository import GstPbutils
GObject.threads_init()



def get_file_info(fname, verbose=False):
    """
    Gets info from video file.
    
    See:
    http://stackoverflow.com/questions/11324519/how-do-i-use-the-discoverer-module-with-pygi-gstpbutils
    
    Hjalmar K. Turesson, 2016-09-22
    """
    
    def on_discovered(discoverer, ismedia, infile):
        pass

    discoverer = GstPbutils.Discoverer()
    discoverer.connect('discovered', on_discovered)
    uri = pathlib.Path(fname).as_uri()
    info = discoverer.discover_uri(uri)

    video_info = {'format': '--',
                  'format_version': '--',
                  'format_specification': '--',
                  'width': -1,
                  'height': -1,
                  'fps': -1}
    
    # video info
    vstreams = info.get_video_streams()
    if len(vstreams) == 1:
        vcaps = vstreams[0].get_caps().to_string()
        if verbose:
            print('Video info:')
            print(vcaps)
        
        vcaps = vcaps.split(',')
    
        for prop in vcaps:
            if 'video/' in prop:
                video_info['format'] = prop
            elif 'version=' in prop:
                video_info['format_version'] = prop.split(')')[-1]
            elif 'format=' in prop:
                video_info['format_specification'] = prop.split(')')[-1]            
            elif 'width=' in prop:
                video_info['width'] = int(prop.split(')')[-1])
            elif 'height=' in prop:
                video_info['height'] = int(prop.split(')')[-1])
            elif 'framerate=' in prop:
                if 'fraction' in prop:
                    video_info['fps'] = eval(prop.split(')')[-1])
                elif 'in' in prop:
                    video_info['fps'] = int(prop.split(')')[-1])
                else:
                    video_info['fps'] = prop.split(')')[-1]
    else:
        print('%d video streams found, expected 1' % len(vstreams))

    # audio info
    astreams = info.get_audio_streams()
    if len(astreams):
        acaps = []
        for astream in astreams:
            acaps.append(astream.get_caps().to_string())
            
        if verbose:
            print('Audio info:')
            print(acaps)
            
        audio_info = acaps
    else:
        if verbose:
            print('No audio stream found')
        
        audio_info = []
    
    
    return video_info, audio_info


class Webcam_h264:
    def __init__(self, video_dev='/dev/video0',
                 audio_dev=None,
                 fps=30,
                 t_start=0.0):
        """
        Understand audio_dev:
            hw:X,Y comes from this mapping of audio hardware
            X is the card number, while Y is the device number.
            Use 'arecord -l' to list the available cards and devices.
            http://jan.newmarch.name/LinuxSound/Sampled/Alsa/
        """

        if audio_dev is None:
            audio = False
        elif audio_dev is 'default':
            audio_dev = "hw:2,0"
            audio = True

        ts_log_fname = 'webcam_h264_timestamps.log'
        vid_fname = 'webcam_h264.mkv'
        self.ts_log = open(ts_log_fname, 'w')
        self.ts_log.write('video filename: %s, t_start: %0.9f'
                          '\nframe_number, offset_ts, pts, cam_running_ts\n' %
                          (vid_fname, t_start))
        if not t_start:
            self.t_start = datetime.now().timestamp()
        else:
            self.t_start = t_start

        self.n_frames = 0

        # Create GStreamer pipline
        self.pipeline = Gst.Pipeline()

        # Create bus to get events from GStreamer pipeline
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message::error', self.on_error)

        # This is needed to make the video output in our DrawingArea:
        self.bus.enable_sync_message_emission()
        self.bus.connect('sync-message::element', self.on_sync_message)

        ###########################
        # Callable function
        ###########################
        def on_new_sample(appsink):
            """
            Function called from the pipeline by appsink.
            Writes the timestampes of frame capture to a log file.
            """
            # Get the buffer
            smp = appsink.emit('pull-sample')
            buf = smp.get_buffer()
            timestamp = np.float64(1e-9) * buf.pts + self.offset_t
            timestamp1 = datetime.now().timestamp() - self.t_start
            self.n_frames += 1
            self.ts_log.write('%d,%0.9f,%0.9f,%0.9f\n' %
                              (self.n_frames,
                               timestamp,
                               buf.pts / Gst.SECOND,
                               timestamp1))
            return False

        ###########################
        # Create GStreamer elements
        ###########################
        # Video source:
        self.v4l2src0 = Gst.ElementFactory.make('v4l2src', None)
        self.v4l2src0.set_property('device', video_dev)
        self.v4l2src0.set_property('do-timestamp', 'true')
        # Video source filters:
        vid0caps = Gst.Caps.from_string('video/x-h264,width=%d,height=%d,'
                                        'framerate=%d/1' % (1280, 720, fps))
        self.vid0filter = Gst.ElementFactory.make('capsfilter', None)
        self.vid0filter.set_property('caps', vid0caps)
        # Parse video:
        self.vid0parse = Gst.ElementFactory.make('h264parse', None)
        # Split:
        self.tee0 = Gst.ElementFactory.make('tee', None)
        self.tee0.set_property('name', 't0')
        ####
        # Display branch
        ####
        # Decode
        self.vid0decode = Gst.ElementFactory.make('avdec_h264', None)
        # Scale to display size:
        self.disp0scale = Gst.ElementFactory.make('videoscale', None)
        # Display filter caps:
        disp0caps = Gst.Caps.from_string('video/x-raw,width=%d,height=%d' %
                                         (800, 600))
        # Sinks:
        self.disp0sink = Gst.ElementFactory.make('autovideosink', None)
        self.disp0sink.set_property('filter-caps', disp0caps)
        ####
        # File branch
        ####
        self.mux = Gst.ElementFactory.make('matroskamux', None)
        self.file0sink = Gst.ElementFactory.make('filesink', None)
        self.file0sink.set_property('location', vid_fname)
        self.file0sink.set_property('sync', False)
        ####
        # Timestamp branch
        ####
        # Create appsink
        self.ts0sink = Gst.ElementFactory.make('appsink', None)
        # Setting properties of appsink
        ts0caps = vid0caps  # use same caps as for camera
        self.ts0sink.set_property('caps', ts0caps)
        self.ts0sink.set_property("max-buffers", 20)  # Limit memory usage
        # Tell sink to emit signals
        self.ts0sink.set_property('emit-signals', True)
        self.ts0sink.set_property('sync', False)  # No sync
        # Connect appsink to my function (writing timestamps)
        self.ts0sink.connect('new-sample', on_new_sample)

        self.queue0 = Gst.ElementFactory.make('queue', None)
        self.queue1 = Gst.ElementFactory.make('queue', None)
        self.disp_queue = Gst.ElementFactory.make('queue', None)
        self.file_queue = Gst.ElementFactory.make('queue', None)
        self.ts_queue = Gst.ElementFactory.make('queue', None)

        #self.queue = Gst.ElementFactory.make('queue', None)

        if audio:
            # Audio source:
            self.alsasrc0 = Gst.ElementFactory.make('alsasrc')
            self.alsasrc0.set_property('device', audio_dev)
            # Audio source filters:
            aud0caps = Gst.Caps.from_string('audio/x-raw,'
                                            'format=S16LE'
                                            'rate=44100,'
                                            'channels=1')
            self.aud0filter = Gst.ElementFactory.make('capsfilter', None)
            self.aud0filter.set_property('caps', aud0caps)
            # Encode audio:
            self.audconv = Gst.ElementFactory.make('audioconvert', None)
            self.audenc = Gst.ElementFactory.make('flacenc', None)
            self.aud_queue = Gst.ElementFactory.make('queue', None)

        # Add elements to the pipeline
        self.pipeline.add(self.v4l2src0)
        self.pipeline.add(self.vid0filter)
        self.pipeline.add(self.vid0parse)
        self.pipeline.add(self.tee0)
        self.pipeline.add(self.vid0decode)
        self.pipeline.add(self.disp0scale)
        self.pipeline.add(self.disp0sink)
        self.pipeline.add(self.mux)
        self.pipeline.add(self.file0sink)
        self.pipeline.add(self.ts0sink)
        self.pipeline.add(self.queue0)
        self.pipeline.add(self.queue1)
        self.pipeline.add(self.disp_queue)
        self.pipeline.add(self.file_queue)
        self.pipeline.add(self.ts_queue)

        if audio:
            self.pipeline.add(self.alsasrc0)
            self.pipeline.add(self.aud0filter)
            self.pipeline.add(self.audconv)
            self.pipeline.add(self.audenc)
            self.pipeline.add(self.aud_queue)

        ###############
        # Link elements
        ###############
        # video source
        if not self.v4l2src0.link(self.vid0filter):
            print('video source to video filter link failed')
        if not self.vid0filter.link(self.vid0parse):
            print('video filter to video parse link failed')
        if not self.vid0parse.link(self.tee0):
            print('video parse to tee link failed')
        if audio:
            # audio source
            if not self.alsasrc0.link(self.aud0filter):
                print('audio source to audio filter link failed')
            if not self.aud0filter.link(self.audconv):
                print('audio filter to audio convert link failed')
            if not self.audconv.link(self.audenc):
                print('audio convert to audio enconder link failed')
            if not self.audenc.link(self.aud_queue):
                print('audio enconder to audio queue link failed')
        # tee
        if not self.tee0.link(self.disp_queue):
            print('tee to display queue link failed')
        if not self.tee0.link(self.file_queue):
            print('tee to file queue link failed')
        if not self.tee0.link(self.ts_queue):
            print('tee to ts queue link failed')
        # video display sink
        if not self.disp_queue.link(self.vid0decode):
            print('dispaly queue to video decode link failed')
        if not self.vid0decode.link(self.disp0scale):
            print('decode to videoscale link failed')
        if not self.disp0scale.link(self.queue0):
            print('disp0scale to queue0 link failed')
        if not self.queue0.link_filtered(self.disp0sink, disp0caps):
            print('queue0 to display-sink link failed')
        # file sink
        if not self.file_queue.link(self.mux):
            print('file queue to mux link failed')
        if audio:
            if not self.aud_queue.link(self.mux):
                print('audio queue to mux link failed')
        if not self.mux.link(self.queue1):
            print('mux to queue1 link failed')
        if not self.queue1.link(self.file0sink):
            print('queue1 to file-sink link failed')
        # timestamp sink
        if not self.ts_queue.link(self.ts0sink):
            print('ts queue to ts-sink link failed')

    def run(self):
        self.offset_t = datetime.now().timestamp() - self.t_start
        self.pipeline.set_state(Gst.State.PLAYING)

    def close(self):
        self.pipeline.set_state(Gst.State.NULL)
        self.ts_log.close()

    def on_sync_message(self, bus, msg):
        if msg.get_structure().get_name() == 'prepare-window-handle':
            msg.src.set_property('force-aspect-ratio', True)

    def on_error(self, bus, msg):
        print('on_error():', msg.parse_error())


class Webcam:
    """
    Test commands:
    raw, mjpg or h264 from camera
    ----------------
    Single to display:
    gst-launch-1.0 -v v4l2src device=/dev/video0 !
            video/x-h264,width=640,height=480,framerate=15/1 !
            h264parse ! avdec_h264 ! xvimagesink sync=false
    Dual to display and file:
    gst-launch-1.0 -v v4l2src device=/dev/video0 !
            video/x-h264,width=640,height=480,framerate=15/1 !
            tee name=t t. ! queue ! h264parse ! avdec_h264 !
            xvimagesink sync=false t. ! queue ! h264parse ! matroskamux !
            filesink location='h264_dual.mkv' sync=false

    Raw from camera
    ---------------
    Single to display:
    gst-launch-1.0 -v v4l2src device=/dev/video0 !
            video/x-raw,format=YUY2,width=640,height=480,framerate=15/1 !
            xvimagesink sync=false
    Single to encoded to file:
    gst-launch-1.0 -v v4l2src device=/dev/video0 !
            video/x-raw,format=YUY2,width=640,height=480,framerate=15/1 !
            videoconvert ! x264enc ! matroskamux !
            filesink location='raw_single.mkv' sync=false
    Dual to display and encoded to file:
    gst-launch-1.0 -v v4l2src device=/dev/video0 !
            video/x-raw,format=YUY2,width=640,height=480,framerate=15/1 !
            tee name=t t. ! queue ! xvimagesink sync=false t. ! queue !
            videoconvert ! x264enc ! h264parse ! matroskamux !
            filesink location='raw_dual.mkv' sync=false
    gst-launch-1.0 -v v4l2src device=/dev/video0 !
            video/x-raw,format=YUY2,width=640,height=480,framerate=15/1 !
            tee name=t t. ! queue ! xvimagesink sync=false t. ! queue !
            videoconvert ! x264enc tune=zerolatency ! h264parse ! matroskamux !
            filesink location='raw_dual.mkv' sync=false

    gst-launch-1.0 -v v4l2src device=/dev/video0 !
            video/x-raw,format=YUY2,width=640,height=480,framerate=15/1 !
            tee name=t t. ! queue ! xvimagesink sync=false t. ! queue !
            videoconvert ! theoraenc ! theoraparse ! matroskamux !
            filesink location='raw_dual.mkv' sync=false

    gst-launch-1.0 -v v4l2src device=/dev/video0 !
            video/x-raw,format=YUY2,width=640,height=480,framerate=15/1 !
            tee name=t t. ! queue ! xvimagesink sync=false t. ! queue !
            videoconvert ! vp8enc ! matroskamux !
            filesink location='raw_dual.mkv' sync=false

    Good info on v4l2src and webcam stream:
    http://blog.buberel.org/2008/03/but-wait-theres.html

    Textoverlay / draw_timestamp info:
    http://stackoverflow.com/questions/22469913/gstreamer-textoverlay-is-not-
            dynamically-updated-during-play
    https://blogs.gnome.org/uraeus/2012/11/08/gstreamer-python-and-videomixing/
    """
    def __init__(self,
                 video_fname=None,
                 tslog_fname=None,
                 video_dev='/dev/video0',
                 audio_dev=None,
                 fps=15,
                 t_start=0.0,
                 display=True,
                 timestamp=True,
                 srcenc='mjpeg',
                 writeenc='theora',
                 draw_timestamp=True):
        """
        Parameters
        ----------
        video_fname    : File name, including path, of saved video file.
                         If it does not end with .mkv this will be appended.
                         Default: None, no file will be written.
        tslog_fname    : File name, including path,
                         of saved timestamp log file.
                         Default: video_fname[:-3] + log
        video_dev      :
        audio_dev      :
        fps            :
        t_start        :
        display        :
        display        :
        timestamp      :
        srcenc         : raw or ..?
        writeenc       : Encoding of saved video.
                         Alternatives: 'theora'(default), 'vp8' and 'mp4'.
                         Theora probably produces the most reliavle timestamps.
        dra_timestamp  : Whether to draw timestamp on saved video.
                         Default: True

        Returns/output
        --------------


        Understand audio_dev:
            hw:X,Y comes from this mapping of audio hardware
            X is the card number, while Y is the device number.
            Use 'arecord -l' to list the available cards and devices.

        """
        if not video_fname is None:
            self.write = True
            if not video_fname.endswith('.mkv'):
                print('.mkv is appended to video_fname')
                video_fname = '%s.mkv' % video_fname

            if tslog_fname is None:
                tslog_fname = '%slog' % video_fname[:-3]
        else:
            self.write = False

        self.timestamp = timestamp
        self.display = display
        if not self.timestamp:
            draw_timestamp = False
        self.draw_timestamp = draw_timestamp

        if audio_dev is None:
            self.audio = False
        elif audio_dev is 'default':
            audio_dev = "hw:2,0"
            self.audio = True

        writeparse = None
        writeenc_prop = None
        if 'theora' in writeenc:
            writeenc = 'theoraenc'
            writeenc_caps = 'video/x-theora'
            writeparse = 'theoraparse'
        elif 'vp8' in writeenc:
            writeenc = 'vp8enc'
            writeenc_caps = 'video/x-vp8'
        elif '264' in writeenc:
            writeenc = 'x264enc'
            writeenc_caps = 'video/x-h264'
            writeparse = 'h264parse'
            writeenc_prop = ('tune', 'zerolatency')

        srcparse = None
        srcdec = None
        if 'raw' in srcenc:
            src_caps = ('video/x-raw,'
                        'format=YUY2,'
                        'width=%d,height=%d,'
                        'framerate=%d/1' % (640, 480, fps))
        elif 'peg' in srcenc:
            src_caps = ('image/jpeg,'
                        'width=%d,height=%d,'
                        'framerate=%d/1' % (640, 480, fps))
            srcdec = 'jpegdec'
        elif '264' in srcenc:
            src_caps = ('video/x-h264,'
                        'width=%d,height=%d,'
                        'framerate=%d/1' % (640, 480, fps))
            srcparse = 'h264parse'
            srcdec = 'avdec_h264'

        skip_encdec = False
        if ('264' in srcenc) and (writeenc is 'x264enc'):
            skip_encdec = True
            srcdec = None

        if self.timestamp:
            self.ts_log = open(tslog_fname, 'w')
            self.ts_log.write('video fname: %s, t_start: %0.9f\nframe_number, '
                              'offset_ts, cam_running_ts, python_ts\n' %
                              (video_fname, t_start))
        if not t_start:
            self.t_start = datetime.now().timestamp()
        else:
            self.t_start = t_start

        self.n_frames = 0

        # Create GStreamer pipline
        self.pipeline = Gst.Pipeline()
        # Create bus to get events from GStreamer pipeline
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message::error', self.on_error)
        # This is needed to make the video output in our DrawingArea:
        self.bus.enable_sync_message_emission()
        self.bus.connect('sync-message::element', self.on_sync_message)

        ###########################
        # Create GStreamer elements
        ###########################
        # Video source:
        self.v4l2src0 = Gst.ElementFactory.make('v4l2src', None)
        self.v4l2src0.set_property('device', video_dev)
        self.v4l2src0.set_property('do-timestamp', 'true')
        # Video source filters
        # Formats available from C920 camera:
        #   'image/jpeg', 'video/x-h264', 'video/x-raw'
        vid_caps = Gst.Caps.from_string(src_caps)
        self.vid_filter = Gst.ElementFactory.make('capsfilter', None)
        self.vid_filter.set_property('caps', vid_caps)
        if not srcparse is None:
            self.src_parse = Gst.ElementFactory.make(srcparse, None)
        if not srcdec is None:
            self.read_dec = Gst.ElementFactory.make(srcdec, None)
        # Split:
        self.tee0 = Gst.ElementFactory.make('tee', None)
        ####
        # File branch
        ####
        if self.write:
            # Encode
            self.file_queue = Gst.ElementFactory.make('queue', None)
            if not skip_encdec:
                self.vid_conv = Gst.ElementFactory.make('videoconvert')
                self.vid_enc = Gst.ElementFactory.make(writeenc, None)
                if not writeenc_prop is None:
                    self.vid_enc.set_property(writeenc_prop[0],
                                              writeenc_prop[1])
                if not writeparse is None:
                    self.vid_parse = Gst.ElementFactory.make(writeparse, None)
            self.mux = Gst.ElementFactory.make('matroskamux', None)
            mux_caps = Gst.Caps.from_string('%s,'
                                            'width=%d,height=%d,'
                                            'framerate=%d/1' %
                                            (writeenc_caps, 640, 480, fps))
            self.file_sink = Gst.ElementFactory.make('filesink', None)
            self.file_sink.set_property('location', video_fname)
            self.file_sink.set_property('sync', False)

        ####
        # Timestamp branch
        ####
        if self.timestamp:
            if self.draw_timestamp:
                self.textoverlay = Gst.ElementFactory.make('textoverlay', None)
                #self.textoverlay.set_property("halign", "left")
                #self.textoverlay.set_property("valign", "top")
                #self.textoverlay.set_property("shaded-background", "true")
            self.ts_queue = Gst.ElementFactory.make('queue', None)
            # Create appsink
            self.ts_sink = Gst.ElementFactory.make('appsink', None)
            # Tell sink to emit signals
            self.ts_sink.set_property('emit-signals', True)
            self.ts_sink.set_property('sync', False)  # No sync
            self.ts_sink.set_property('drop', False)
            # Connect appsink to my function (writing timestamps)
            self.ts_sink.connect('new-sample', self.on_new_sample)

        ####
        # Display branch
        ####
        if self.display:
            self.disp_queue = Gst.ElementFactory.make('queue', None)
            if skip_encdec:
                self.disp_dec = Gst.ElementFactory.make('avdec_h264', None)
            # Scale to display size:
            self.disp_scale = Gst.ElementFactory.make('videoscale', None)
            # Display filter caps:
            disp_caps = Gst.Caps.from_string('video/x-raw,'
                                             'width=%d,height=%d' %
                                             (640, 480))
            # display sink:
            self.disp_sink = Gst.ElementFactory.make('xvimagesink', None)
            self.disp_sink.set_property('sync', False)
            #self.disp0sink.set_property('filter-caps', disp0caps)

        if self.audio:
            # Audio source:
            self.alsasrc0 = Gst.ElementFactory.make('alsasrc')
            self.alsasrc0.set_property('device', audio_dev)
            # Audio source filters:
            aud_caps = Gst.Caps.from_string('audio/x-raw,'
                                            'format=S16LE'
                                            'rate=44100,'
                                            'channels=1')
            self.aud_filter = Gst.ElementFactory.make('capsfilter', None)
            self.aud_filter.set_property('caps', aud_caps)
            # Encode audio:
            self.audconv = Gst.ElementFactory.make('audioconvert', None)
            self.audenc = Gst.ElementFactory.make('flacenc', None)
            self.aud_queue = Gst.ElementFactory.make('queue', None)

        # Add elements to the pipeline
        self.pipeline.add(self.v4l2src0)
        self.pipeline.add(self.vid_filter)
        if not srcparse is None:
            self.pipeline.add(self.src_parse)
        if not srcdec is None:
            self.pipeline.add(self.read_dec)
        self.pipeline.add(self.tee0)

        if self.write:
            self.pipeline.add(self.file_queue)
            if not skip_encdec:
                self.pipeline.add(self.vid_conv)
                self.pipeline.add(self.vid_enc)
                if not writeparse is None:
                    self.pipeline.add(self.vid_parse)
            self.pipeline.add(self.mux)
            self.pipeline.add(self.file_sink)
        if self.timestamp:
            if self.draw_timestamp:
                self.pipeline.add(self.textoverlay)
            self.pipeline.add(self.ts_sink)
            self.pipeline.add(self.ts_queue)

        if self.display:
            self.pipeline.add(self.disp_scale)
            if skip_encdec:
                self.pipeline.add(self.disp_dec)
            self.pipeline.add(self.disp_sink)
            self.pipeline.add(self.disp_queue)

        if self.audio:
            self.pipeline.add(self.alsasrc0)
            self.pipeline.add(self.aud_filter)
            self.pipeline.add(self.aud_conv)
            self.pipeline.add(self.aud_enc)
            self.pipeline.add(self.aud_queue)

        ###############
        # Link elements
        ###############
        # video source
        if not self.v4l2src0.link(self.vid_filter):
            print('video source to video filter link failed')
        if not srcparse is None:
            if not self.vid_filter.link(self.src_parse):
                print('video filter to video parse link failed')
            if not srcdec is None:
                if not self.src_parse.link(self.read_dec):
                    print('video parse to video decode link failed')
                if not self.read_dec.link(self.tee0):
                    print('video decode to tee link failed')
            else:
                if not self.src_parse.link(self.tee0):
                    print('video parse to video decode link failed')
        else:
            if not srcdec is None:
                if not self.vid_filter.link(self.read_dec):
                    print('video filter to video decode link failed')
                if not self.read_dec.link(self.tee0):
                    print('video decode to tee link failed')
            else:
                if not self.vid_filter.link(self.tee0):
                    print('video filter to tee link failed')

        if self.write:
            if not self.tee0.link(self.file_queue):
                print('tee to file queue link failed')
            if self.draw_timestamp:
                if not self.file_queue.link(self.textoverlay):
                    print('file queue to textoverlay link failed')
                if not skip_encdec:
                    if not self.textoverlay.link(self.vid_conv):
                        print('textoverlay to video converter link failed')
                    if not self.vid_conv.link(self.vid_enc):
                        print('video converter to video encoder link failed')
                    if not writeparse is None:
                        if not self.vid_enc.link(self.vid_parse):
                            print('video encoder to video parser link failed')
                        if not self.vid_parse.link_filtered(self.mux,
                                                            mux_caps):
                            print('video encoder to filter & mux link failed')
                    else:
                        if not self.vid_enc.link_filtered(self.mux, mux_caps):
                            print('video encoder to filter & mux link failed')
                else:
                    if not self.textoverlay.link_filtered(self.mux, mux_caps):
                        print('textoverlay to filter & mux link failed')
            else:
                if not skip_encdec:
                    if not self.file_queue.link(self.vid_conv):
                        print('file queue to video converter link failed')
                    if not self.vid_conv.link(self.vid_enc):
                        print('video converter to video encoder link failed')
                    if not writeparse is None:
                        if not self.vid_enc.link(self.vid_parse):
                            print('video encoder to video parser link failed')
                        if not self.vid_parse.link_filtered(self.mux,
                                                            mux_caps):
                            print('video encoder to filter & mux link failed')
                    else:
                        if not self.vid_enc.link_filtered(self.mux, mux_caps):
                            print('video encoder to filter & mux link failed')
                else:
                    if not self.file_queue.link_filtered(self.mux, mux_caps):
                        print('file queue to filter & mux link failed')
            if not self.mux.link(self.file_sink):
                print('mux to file-sink link failed')

        if self.timestamp:
            # timestamp sink
            if not self.tee0.link(self.ts_queue):
                print('tee to ts queue link failed')
            if not self.ts_queue.link(self.ts_sink):
                print('ts queue to ts-sink link failed')

        if self.display:
            # video display sink
            if not self.tee0.link(self.disp_queue):
                print('tee to display queue link failed')
            if skip_encdec:
                if not self.disp_queue.link(self.disp_dec):
                    print('display queue to display decode link failed')
                if not self.disp_dec.link(self.disp_scale):
                    print('display decode to display scale link failed')
            else:
                if not self.disp_queue.link(self.disp_scale):
                    print('display queue to display scale link failed')
            if not self.disp_scale.link_filtered(self.disp_sink, disp_caps):
                print('display scale to display-sink link failed')

        if self.audio:
            # audio source
            if not self.alsasrc0.link(self.aud_filter):
                print('audio source to audio filter link failed')
            if not self.aud_filter.link(self.aud_conv):
                print('audio filter to audio convert link failed')
            if not self.aud_conv.link(self.aud_enc):
                print('audio convert to audio enconder link failed')
            if not self.aud_enc.link(self.aud_queue):
                print('audio enconder to audio queue link failed')
            if not self.aud_queue.link(self.mux):
                print('audio queue to mux link failed')

    ###########################
    # Callable function for writing timestamps
    ###########################
    def on_new_sample(self, appsink):
        """
        Function called from the pipeline by appsink.
        Writes the timestampes of frame capture to a log file.
        """
        # Get the buffer
        smp = appsink.emit('pull-sample')
        buf = smp.get_buffer()
        timestamp = np.float64(1e-9) * buf.pts + self.offset_t
        ts1 = datetime.now().timestamp() - self.t_start
        self.n_frames += 1
        self.ts_log.write('%d,%0.9f,%0.9f,%0.9f\n' %
                          (self.n_frames,
                           timestamp,
                           np.float64(1e-9) * buf.pts,
                           ts1))
        if self.draw_timestamp:
            parsed_ts = ('frame #: %d, ts: %0.5f, pts: %0.5f, ts1: %0.5f\n' %
                         (self.n_frames,
                          timestamp,
                          np.float64(1e-9) * buf.pts,
                          ts1))
            self.textoverlay.set_property('text', parsed_ts)
        return Gst.FlowReturn.OK

    ######################################################
    # Generates the dot file, checks that graphviz in installed
    # and generates a png file, which then displays the pipeline
    ######################################################
    def on_debug_activate(self):
        fn = 'pipeline-debug-graph'
        fn_dot = "%s/%s.dot" % (os.environ.get("GST_DEBUG_DUMP_DOT_DIR"), fn)
        fn_png = "%s/%s.png" % (os.environ.get("GST_DEBUG_DUMP_DOT_DIR"), fn)
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, fn)
        try:
            os.system("dot -Tpng %s > %s" % (fn_dot, fn_png))
            print('Pipline graph written to %s' % fn_png)
            #Gtk.show_uri(None, "file://%s" % fn_png, 0)
        except:
            print('Failure!')
            # check if graphviz is installed with a simple test
            if os.system('which dot'):
                print("Graphviz does not seem to be installed.")

    def run(self):
        self.offset_t = datetime.now().timestamp() - self.t_start
        self.pipeline.set_state(Gst.State.PLAYING)

    def close(self):
        self.pipeline.set_state(Gst.State.NULL)
        if self.timestamp:
            self.ts_log.close()

    def on_sync_message(self, bus, msg):
        if msg.get_structure().get_name() == 'prepare-window-handle':
            msg.src.set_property('force-aspect-ratio', True)

    def on_error(self, bus, msg):
        print('on_error():', msg.parse_error())


class Webcam_ts_minimal_h264:
    def __init__(self, video_dev='/dev/video0', fps=15):
        """
        """
        ts_log_fname = 'ts_test.log'

        self.ts_log = open(ts_log_fname, 'w')
        self.ts_log.write('n_buf, buf.pts\n')
        self.n_frames = 0
        self.pipeline = Gst.Pipeline()

        def on_new_sample(appsink):
            """
            Function called from the pipeline by appsink.
            Writes the timestampes of frame capture to a log file.
            """
            # Get the buffer
            smp = appsink.emit('pull-sample')
            buf = smp.get_buffer()
            self.n_frames += 1
            self.ts_log.write('%d,%0.9f\n' %
                              (self.n_frames, np.float64(1e-9) * buf.pts))
            return Gst.FlowReturn.OK

        # Video source:
        self.v4l2src = Gst.ElementFactory.make('v4l2src', None)
        self.v4l2src.set_property('device', video_dev)
        self.v4l2src.set_property('do-timestamp', 'true')
        # Formats available from C920 camera:
        #   'image/jpeg', 'video/x-h264', 'video/x-raw'
        vid_caps = Gst.Caps.from_string('video/x-h264,'
                                        'width=%d,height=%d,'
                                        'framerate=%d/1' % (640, 480, fps))
        self.vid_filter = Gst.ElementFactory.make('capsfilter', None)
        self.vid_filter.set_property('caps', vid_caps)
        self.vid_parse = Gst.ElementFactory.make('h264parse', None)
        self.ts_sink = Gst.ElementFactory.make('appsink', None)
        # Setting properties of appsink
        # Tell sink to emit signals
        self.ts_sink.set_property('emit-signals', True)
        self.ts_sink.set_property('sync', False)  # No sync
        # Connect appsink to my function (writing timestamps)
        self.ts_sink.connect('new-sample', on_new_sample)

        # Add elements to the pipeline
        self.pipeline.add(self.v4l2src)
        self.pipeline.add(self.vid_filter)
        self.pipeline.add(self.vid_parse)
        self.pipeline.add(self.ts_sink)

        # link
        self.v4l2src.link(self.vid_filter)
        self.vid_filter.link(self.vid_parse)
        self.vid_parse.link(self.ts_sink)

    def run(self):
        self.pipeline.set_state(Gst.State.PLAYING)

    def close(self):
        self.pipeline.set_state(Gst.State.NULL)
        self.ts_log.close()


class Webcam_ts_minimal_raw:
    def __init__(self, video_dev='/dev/video0', fps=15):
        """
        """
        ts_log_fname = 'ts_test.log'

        self.ts_log = open(ts_log_fname, 'w')
        self.ts_log.write('n_buf, buf.pts, time.perf_counter()\n')
        self.n_frames = 0
        self.t0 = 0.0
        self.pipeline = Gst.Pipeline()

        def on_new_sample(appsink):
            """
            Function called from the pipeline by appsink.
            Writes the timestampes of frame capture to a log file.
            """
            # Get the buffer
            smp = appsink.emit('pull-sample')
            buf = smp.get_buffer()
            pts = np.float64(1e-9) * buf.pts
            t = time.perf_counter() - self.t0
            self.n_frames += 1
            self.ts_log.write('%d,%0.9f,%0.9f\n' % (self.n_frames, pts, t))
            return Gst.FlowReturn.OK

        # Video source:
        self.v4l2src = Gst.ElementFactory.make('v4l2src', None)
        self.v4l2src.set_property('device', video_dev)
        self.v4l2src.set_property('do-timestamp', 'true')
        # Formats available from C920 camera:
        #   'image/jpeg', 'video/x-h264', 'video/x-raw'
        vid_caps = Gst.Caps.from_string('video/x-raw,'
                                        'format=YUY2,'
                                        'width=%d,height=%d,'
                                        'framerate=%d/1' % (640, 480, fps))
        self.vid_filter = Gst.ElementFactory.make('capsfilter', None)
        self.vid_filter.set_property('caps', vid_caps)
        #self.vid_parse = Gst.ElementFactory.make('jpegparse', None)
        self.ts_sink = Gst.ElementFactory.make('appsink', None)
        # Setting properties of appsink
        # Tell sink to emit signals
        self.ts_sink.set_property('emit-signals', True)
        self.ts_sink.set_property('sync', False)  # No sync
        # Connect appsink to my function (writing timestamps)
        self.ts_sink.connect('new-sample', on_new_sample)

        # Add elements to the pipeline
        self.pipeline.add(self.v4l2src)
        self.pipeline.add(self.vid_filter)
        #self.pipeline.add(self.vid_parse)
        self.pipeline.add(self.ts_sink)

        # link
        self.v4l2src.link(self.vid_filter)
        self.vid_filter.link(self.ts_sink)
        #self.vid_filter.link(self.vid_parse)
        #self.vid_parse.link(self.ts_sink)

    def run(self):
        self.t0 = time.perf_counter()
        self.pipeline.set_state(Gst.State.PLAYING)

    def close(self):
        self.pipeline.set_state(Gst.State.NULL)
        self.ts_log.close()


def get_frames_in_interval(vid_fn, ts_fn, interval=[0, -1]):
    """
    Parameters
    ----------
    video_fn     -- filename of video
    tslog_fn     -- filename of timestamp log
    interval     -- list or tuple with t0 and t1, the first and last time
                    in the interval to extract.
    Returns
    -------
    frames       -- The read frames
    n_and_tes    -- frame numbers and timestamps for the frames
    """

    dtypes = [('frame_n', int),
              ('ts', np.float64),
              ('run_ts', np.float64),
              ('py_ts', np.float64),
              ('vid_ts', np.float64)]

    n_and_ts = np.genfromtxt(ts_fn, dtype=dtypes, skip_header=2, delimiter=',')

    reader = VideoReader(vid_fn)

    t0, t1 = interval, interval[1]
    if t1 == -1:  # Until end
        t1 = reader.duration_seconds

    n_and_ts = n_and_ts[n_and_ts['ts'] >= t0]
    n_and_ts = n_and_ts[n_and_ts['ts'] <= t1]

    frames = []
    frames.append(reader.get_frame(n_and_ts[0]['run_ts']))
    t = reader.get_current_position('time')

    i = 0
    while t <= n_and_ts[-1]['run_ts']:
        frames.append(reader.get_next_frame())
        t = reader.get_current_position('time')
        n_and_ts['vid_ts'][i] = t
        i += 1

    reader.close()

    return frames, n_and_ts


class VideoReader:
    """
    For reading video frames one-by-one.
    Reuturns the frames in as a numpy array in RGB format.
    Not meant for playback.

    Follows the snapshot example:
    http://cgit.freedesktop.org/gstreamer/gst-plugins-base/tree/tests/'
            'examples/snapshot/snapshot.c

    By Hjalmar K. Turesson 2015-12-08
    
    gst-launch-1.0 filesrc location=file.ogg ! decodebin ! audioconvert ! audioresample ! autoaudiosink
    """

    def __init__(self, fname):
        """
        """
        
        #vinfo, _ = get_file_info(fname, verbose=False)
        #self.frame_shape = (vinfo['height'], vinfo['width'], 3)
        # self.frame_shape[0], self.frame_shape[1]

        if not os.path.isfile(fname):
            raise ValueError('No such file: %s' % fname)
        #height=%d, width=%d, 
        s = ('filesrc location=%s '
             '! decodebin '
             '! videoconvert '
             '! video/x-raw, format=RGB '
             '! appsink name=sink' % fname)
        self.pipeline = Gst.parse_launch(s)
      
        # get sink
        self.appsink = self.pipeline.get_by_name("sink")
        self.appsink.set_property("max-buffers", 20)  # To limit memory usage
        # set to PAUSED to make the first frame arrive in the sink

        self.pipeline.set_state(Gst.State.PAUSED)
        # Wait for state change, i.e. block for up to 5 s.
        self.pipeline.get_state(5*Gst.SECOND)

        # Pull 1st sample to get framerate
        smp = self.appsink.emit('pull-preroll')
        sinkcaps = smp.get_caps().get_structure(0)
        fps = sinkcaps.get_fraction("framerate")
        if not fps[0]:
            print('Frame rate is missing form file.')
            return None

        # Frame rate, frames per second
        self.fps_frac = (fps[1], fps[2])
        self.fps = fps[1]/fps[2]
        # Nano seconds per frames
        self.dt_ns = int((Gst.SECOND * fps[2]) / fps[1])            
            
        # Get and set frame width
        width = sinkcaps.get_int("width")
        if not width[0]:
            print('Frame width is missing from file.')
            return None        
        self.width = width[1]
        #sinkcaps.set_value("width", self.width)
        # Get and set frame height
        height = sinkcaps.get_int("height")
        if not height[0]:
            print('Frame height is missing from file.')
            return None
        self.height = height[1]
        #sinkcaps.set_value("height", self.height)
        
        color_format = sinkcaps.get_value('format')
        if 'GRAY' in color_format:   # One color channel?
            self.frame_shape = (self.height, self.width, 1)
        else:
            self.frame_shape = (self.height, self.width, 3)
            
        self.frame_size = np.prod(self.frame_shape)
                
        # Duration of video in nano seconds, seconds and frames
        ret = self.pipeline.query_duration(Gst.Format.TIME)
        self.duration_nanoseconds = None
        if ret[0]:
            self.duration_seconds = ret[1]/Gst.SECOND
            self.duration_nanoseconds = ret[1]
            # Approximate the number of frames.
            self.duration_nframes = int(round(self.duration_seconds * 
                                              self.fps_frac[0] / self.fps_frac[1]))

        if self.duration_nanoseconds is None:
            print('Duration query failed.')
            return None
            
        # To avoid having to call buf.get_size() for every frame
        self.buf_size = None
        
        # for some videos buf.get_size() returned 400 bytes more
        # of data more than np.prod(self.frame_shape). I don't know why.
        # This occurs for videoconvert to RGB and NOT RGBx
        # (but I don't want a height x width x 4 shaped frame)
        self.buf_wrong_size = False
         

    def _get_current_frame(self):
        """
        Reads the frame at the current position.

        Returns:
        frame : An uint8 numpy array in RGB format.
        """

        smp = self.appsink.emit('pull-preroll')

        if not smp is None:
            
            buf = smp.get_buffer()
            
            if self.buf_size is None:
                self.buf_size = buf.get_size()
                # Check for the buffer/frame size discrepancy

                # for some videos buf.get_size() returned 400 bytes more
                # of data more than np.prod(self.frame_shape). I don't know why.
                # This occurs for videoconvert to RGB and NOT RGBx
                # (but I don't want a height x width x 4 shaped frame)
                if self.buf_size > self.frame_size:
                    self.buf_wrong_size = True            
                    # this hack seems to fix it
                    step = self.buf_size // np.prod(self.frame_shape[0])
                    rm_ix = np.arange(self.frame_shape[0]) * step
                    self.keep_ix = np.ones(self.buf_size, dtype=bool)
                    self.keep_ix[rm_ix-1] = False
                    self.keep_ix[rm_ix-2] = False
            
            data = np.fromstring(buf.extract_dup(0, self.buf_size), dtype='uint8')

            if self.buf_wrong_size:
                data = data[self.keep_ix]            


            return data.reshape(self.frame_shape)

        else:
            return None

    def get_frame(self, t):
        """
        Reads a frame at time t.

        Parameter:
        ---------
        t : time of frame in seconds.

        Returns:
        frame : An uint8 numpy array in RGB format.
        """
        t = int(t * Gst.SECOND)
        if self.duration_nanoseconds < t:
            print('Requested frame time is after end of video.')
            return None

        ret = self.pipeline.seek_simple(Gst.Format.TIME,
                                        Gst.SeekFlags.FLUSH,
                                        t)
        # Wait for state change, i.e. block for up to 5 s.
        self.pipeline.get_state(5*Gst.SECOND)
        if not ret:
            print('Seek to time %1.4f s failed.' % t/Gst.SECOND)
            return None
        else:
            return self._get_current_frame()

    def get_next_frame(self):
        """
        Reads the next frame from current position.

        Returns:
        frame : An uint8 numpy array in RGB format.
        """
        ret = self.pipeline.query_position(Gst.Format.TIME)
        if not ret[0]:
            print('Position query failed.')
            return 0

        
        t = ret[1] + self.dt_ns

        # TODO:
        # Seems like pipeline.query_position(Gst.Format.BUFFERS) failes to
        # return current position in number of frames.
        # Fix this and frame stepping can be checked.

#        # Get postion before seek
#        ret = self.pipeline.query_position(Gst.Format.BUFFERS)
#        if not ret[0]:
#            print('Failed to get to current position.')
#            return None
#        else:
#            old_pos = ret[1]

        if self.duration_nanoseconds < t:
            print('Requested frame time is after end of video.')
            return None

        ret = self.pipeline.seek_simple(Gst.Format.TIME,
                                        Gst.SeekFlags.FLUSH,
                                        t)
        # Wait for state change, i.e. block for up to 5 s.
        self.pipeline.get_state(5*Gst.SECOND)
        if not ret:
            print('Seek to frame at time %1.4f s failed.' % t/Gst.SECOND)
            return None

        # TODO:
        # Seems like pipeline.query_position(Gst.Format.BUFFERS) failes to
        # return current position in number of frames.
        # Fix this and frame stepping can be checked.

#        # Check that we really only stepped 1 frame.
#        ret = self.pipeline.query_position(Gst.Format.BUFFERS)
#        if not ret[0]:
#            print('Failed to get new position.')
#            return None
#        else:
#            new_pos = ret[1]
#            if not (old_pos + 1) == new_pos:
#                print('Failed to step one frame.\nOld frame num:'
#                      ' %d, new frame num: %d' % (old_pos, new_pos))
#            return None
        else:
            return self._get_current_frame()

    def get_current_position(self, fmt='time'):
        """
        Returns the current postion in time (seconds) or frame number.

        Parameters:
        ----------
        fmt : format for position.
              'time' returns time in seconds or
              'frame_number' as the number of frames from the beginning to
              current position.
        """

        pos = None
        if fmt == 'time':
            ret = self.pipeline.query_position(Gst.Format.TIME)
            if ret[0]:
                pos = ret[1]/Gst.SECOND
        elif fmt == 'frame_number':
            ret = self.pipeline.query_position(Gst.Format.BUFFERS)
            if ret[0] and ret[1] >= 0:  # TODO: ret[0] == True even when the
                                        # frame number is 0, i.e. the query
                                        # failed.
                                        # Thus, ret[1] has to be >= 0.
                pos = ret[1]
            else:
                ret = self.pipeline.query_position(Gst.Format.TIME)
                if ret[0]:
                    pos = int(round((ret[1]/Gst.SECOND)*self.fps))

        else:
            print('%s is not a vaild format. '
                  'Valid formats are "time" | "frame_number"' % fmt)

        if pos is None:
            print('Position query failed.')

        return pos

    def close(self):
        """
        Closes the pipeline.
        """
        self.pipeline.set_state(Gst.State.NULL)
        # Wait for state change, i.e. block for up to 5 s.
        self.pipeline.get_state(5*Gst.SECOND)
