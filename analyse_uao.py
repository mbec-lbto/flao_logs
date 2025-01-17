#!/usr/bin/env python3

import csv
import os, time, getopt, sys, calendar, re
import glob
import operator, argparse
from functools import reduce

parser = argparse.ArgumentParser('UAO log analysis')
parser.add_argument('day', type=str, help='UTC date YYYYMMDD format')
parser.add_argument('side', choices=['R', 'L'], help='side')
parser.add_argument('logdir', type=str, help='directory where log files are stored')
parser.add_argument('--html', action='store_true', default=False, help='generate html output')
parser.add_argument('--outdir', type=str, default='.', help='output directory for csv files (default: %(default)s)')
parser.add_argument('--verbose', action='store_true', default=False, help='verbose output')
args = parser.parse_args()

def log_timestamp(line):
    fields = line.split('|')
    timestamp, microsec = fields[3].split('.')
    return calendar.timegm(time.strptime(timestamp, '%Y-%m-%d %H:%M:%S')) + float(microsec)/1e6

def julianDayFromUnix(timestamp):
    return (timestamp / 86400.0) + 2440587.5;

def timeStr(t):
    return time.strftime('%Y%m%d %H:%M:%S', time.gmtime(t))

def dayStr(t):
    return time.strftime('%Y%m%d', time.gmtime(t))

def hourStr(t):
    return time.strftime('%H:%M:%S', time.gmtime(t))


def logfile(name, grep=None):
    ''''
    Returns a file-like object to read log files
    '''
    y = args.day[0:4]
    m = args.day[4:6]
    d = args.day[6:8]
    glob_pattern = f'{args.logdir}/{y}/{m}/{d}/{name}.{args.side}.{args.day}[0-9][0-9][0-9][0-9].log*'
    logfiles = ' '.join(sorted(glob.glob(glob_pattern)))

    if not logfiles: raise Exception(f'Cannot find log file(s) matching: {glob_pattern}')

    if args.verbose: print(f'Reading grep: "{grep}" glob: {logfiles}', file=sys.stderr)

    # zcat/zgrep allow compressed or uncompressed files
    if grep is None:
        return os.popen(f'zcat {logfiles}')
    else:
        return os.popen(f'zgrep "{grep}" {logfiles}')

def search(name, string=None, mindiff=1, getDict=False):

    found = logfile(name, grep=string)
    prev=0
    found2={}
    p = re.compile('\>  \. (.*)')
    for f in found:
        now = log_timestamp(f)
        if now-prev>= mindiff:
            if not now in found2:
                found2[now] = f.strip()
            else:
                try:
                    fields = f.split('|')
                    m = p.search(fields[4])
                    if m:
                        found2[now] += m.group(1)
                except IndexError as e:
                    print('Malformed line: '+f)
        else:
            if args.verbose:
                print('Rejected '+f.strip(), file=sys.stderr)
        prev=now

    if getDict:
        return found2

    found3 = []
    for k in sorted(found2.keys()):
        found3.append(found2[k])
    return found3


def myRound(x, ndigits=0):
    '''Returns a rounded number where -0.0 is set to 0'''
    y = round(x, ndigits)
    if y == 0.0:   # This yields True for both 0.0 and -0.0
       y = 0.0
    return y


class Event:
    def __init__(self, name, t, details=None):
        self.name = name
        self.t = t
        self.details = details

    def htmlHeader(self):
        return '<tr><th>Timestamp</th><th>Event</th><th>Details</th></tr>'

    def htmlRow(self):
        return '<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % ( timeStr(self.t), self.name, self.details)


class SkipFrameEvent(Event):
    def __init__(self, t, details=''):
        Event.__init__( self, 'SkipFrame', t, details)

    @staticmethod
    def fromLogLine(line):
        t = log_timestamp(line)
        line = line.replace('->', '--') # Avoid extra ">"
        log, msg = line.split('>')
        return SkipFrameEvent( t, msg)


class FailedActuatorEvent(Event):
    def __init__(self, t, details, actno=None):
        Event.__init__( self, 'FailedActuator', t, details)
        self.actno = actno

    @staticmethod
    def fromLogLine(line):
        t = log_timestamp(line)
        pattern = 'Failing actuator detected N. (\d+)(.*)'
        found = re.findall( pattern, line)
        if len(found) > 0:
            actno, reason = found[0]
            log, msg = line.split('>')
        return FailedActuatorEvent( t, 'Act: %s - %s' % (actno, msg), int(actno))


class RIPEvent(Event):
    def __init__(self, t, details):
        Event.__init__( self, 'RIP', t, details)

    @staticmethod
    def fromLogLine(line):
        t = log_timestamp(line)
        try:
            procname, other = line.split('_')
            if procname=='fastdiagn':
                procname = 'FastDiagnostic'
            if procname=='housekeeper':
                procname = 'HouseKeeper'
            return RIPEvent( t, 'Detected by %s' % procname)
        except ValueError:
            return RIPEvent( t, '')


class ArbCmd:
    def __init__(self, name, args='', start_time=None, end_time=None, success=None, errstr=''):
        self.name = name
        self.args = args
        self.start_time = start_time
        self.end_time = end_time
        self.success = success
        self.errstr = errstr
        self.floatPattern ='[-+]?\d*\.\d+|d+'
        self.wfsPattern ='wfsSpec = (\w+)WFS'
        self.magPattern ='expectedStarMagnitude = (%s)' % self.floatPattern
        self.refXPattern = 'roCoordX = (%s)' % self.floatPattern
        self.refYPattern = 'roCoordY = (%s)' % self.floatPattern
        self.modePattern = 'mode = (\w+)'
 
    def report(self):
        time_str = timeStr(self.start_time)
        if self.success is True:
            success_str = 'Success'
        elif self.success is False:
            success_str = 'Failure: %s' % self.errstr
        else:
            success_str = 'Unknown'

        return '%s %s %s' % (time_str, success_str, ' - '.join(self.details()))
 
    def errorString(self):
        try:
            if self.name == 'PresetAO':

                str = self.errstr
                replaces = [('presetAO:', ''), 
                            ('WARNING -', ''),
                            ('RETRY:', ''),
                            ('(-20004) WFSARB_ARG_ERROR', ''),
                            ('(-5001) TIMEOUT_ERROR', ''),
                            ('(-5002) VALUE_OUT_OF_RANGE_ERROR', '')]

                for r in replaces:
                    str = str.replace(r[0], r[1])
                return str.strip()
        except:
            pass
        return self.errstr

    def is_instrument_preset(self):
        if not self.name == 'PresetAO':
            return False

        # Make sure mag, refX and refY are there
        _ = self.details()

        return self.refX != 0 or self.refY != 0 

    def details2(self):

        details2=[]
        try:
            if self.name == 'OffsetSequence':
                details2.append('Time paused: %.1fs' % self.time_paused())

            if self.name == 'ExposureSequence':
                details2.append('Time exposing: %.1fs' % self.time_exposing())
        except Exception as e:
            print(e)

        return details2


    def details(self):

        details=[]
        try:
            if self.name == 'OffsetSequence' or self.name == 'OffsetXY':
                coords = map( float, re.findall( self.floatPattern, self.args))
                if len(coords) ==2:
                    details.append('X=%.2f, Y=%.2f mm' % (coords[0], coords[1]))

            if self.name == 'PresetAO':
                wfs = re.findall( self.wfsPattern, self.args)[0]
                mag = float(re.findall( self.magPattern, self.args)[0])
                refX = float(re.findall( self.refXPattern, self.args)[0])
                refY = float(re.findall( self.refYPattern, self.args)[0])
                mode = re.findall(self.modePattern, self.args)[0]
                details.append('%s, star mag= %.1f, posXY= %.1f, %.1f mm, mode = %s' % (wfs, mag, myRound(refX, 1), myRound(refY, 1), mode))
                if hasattr(self, 'intervention'):
                    if self.intervention == True:
                        interventionDesc = 'Intervention mode'
                    else:
                        interventionDesc = 'Automatic mode'
                else:
                    interventionDesc = 'Intervention/automatic mode unknown'
              
                self.wfs = wfs
                self.mag = mag
                self.refX = refX
                self.refY = refY
                self.mode = mode
                details.append(interventionDesc) 

            if self.name == 'CompleteObs':
                tt = self.total_time()
                ot = self.total_open_time()
                if tt != 0:
                    per = ot*100/tt
                else:
                    per = 0
                s = '%s, open shutter: %ds (%d%%)' % (self.wfs, ot, per)
                details.append(s)

            if self.name == 'Acquire':
                details=[]
                if hasattr(self, 'estimatedMag'):
                    details.append('Estimated magnitude: %.1f' % self.estimatedMag)
                if hasattr(self, 'hoBinning'):
                    details.append('Ccd39 binning: %d' % self.hoBinning)
                if hasattr(self, 'hoSpeed'):
                    details.append('Loop speed: %d Hz' % self.hoSpeed)
        except Exception as e:
            print(e)

        return details


def get_AOARB_cmds():

    lines = search('AOARB', string='MAIN', mindiff=0)

    cmds=[]
    curCmd=None

    startCmdFlao = 'FSM (status'
    startCmdUao = 'Request:'

    p1 = re.compile('Request: (.*?)\((.*)\)')
    p2 = re.compile('Request: (.*)')
    p3 = re.compile('has received command \d+ \((.*)\)') # FLAO command

    endCmdFlao = ' successfully completed'
    endCmdUao  = 'Status after command:'

    exceptionStr  = '[AOException]'
    illegalCmdStr = 'Illegal command' 
    interventionStr = 'Intervention:'
    readyForStartStr = 'Status after command: AOArbitrator.ReadyForStartAO'
    estimatedMagStr = 'Estimated magnitude from ccd39: '
    hoBinningStr = 'HO binning  : '
    hoSpeedStr =   'HO speed    : '

    lastAcquireRef=None

    for line in lines:
      try:
        if (startCmdFlao in line) or (startCmdUao in line):

            # Skip this command, that has no effect on FSM
            if 'getLastImage' in line:
                continue

            if curCmd is not None:
                cmds.append(curCmd)
                curCmd = None

            t = log_timestamp(line)
            m1 = p1.search(line)
            m2 = p2.search(line)
            m3 = p3.search(line)
            args = ''
            try:
                if m1:
                    name = m1.group(1)
                    args = m1.group(2)
                elif m2:
                    name = m2.group(1)
                elif m3:
                    name = m3.group(1)
                else:
                    print('Malformed request: '+line)
                    continue
            except IndexError as e:
                print('Malformed request: '+line)
                continue
 
            default_success = None

            curCmd = ArbCmd(name=name, args=args, start_time=t, end_time=None, success=default_success, errstr='')
            if name == 'AcquireRefAO':
                lastAcquireRef = curCmd

        elif (endCmdFlao in line) or (endCmdUao in line):
            t = log_timestamp(line)
            curCmd.end_time = t
            curCmd.success = True
            curCmd.errstr = ''

            if readyForStartStr in line:
                if lastAcquireRef is not None:
                    lastAcquireRef.end_time = t
            continue  # TODO remove?
 
        elif exceptionStr in line:
            pos = line.index(exceptionStr)
            curCmd.errstr = line[pos+len(exceptionStr):].strip()
            curCmd.success = False

        elif illegalCmdStr in line:
            pos = line.index(illegalCmdStr)
            curCmd.errstr = line[pos:].strip()
            curCmd.success = False

        # Detect intervention mode in Presets
        elif interventionStr in line:
            pos = line.index(interventionStr) + len(interventionStr)
            interv = line[pos+1:pos+6]
            if interv[0:4] == 'True':
                curCmd.intervention=True
            else:
                curCmd.intervention=False

        # Detect magnitude estimation in AcquireRef 
        elif estimatedMagStr in line:
            pos = line.index(estimatedMagStr)
            curCmd.estimatedMag = float(line[pos+len(estimatedMagStr):])

        elif hoBinningStr in line:
            pos = line.index(hoBinningStr)
            curCmd.hoBinning = int(line[pos+len(hoBinningStr):])

        elif hoSpeedStr in line:
            pos = line.index(hoSpeedStr)
            curCmd.hoSpeed = int(line[pos+len(hoSpeedStr):].split()[0])

      except Exception as e:
        if args.verbose:
            print(e, file=sys.stderr)
 
    # Store last command
    if curCmd is not None:
        cmds.append(curCmd)

    return cmds


class OffsetSequence(ArbCmd):
    '''A Pause - Offset - Resume sequence'''

    def __init__(self, pause, resume, **kwargs):
        ArbCmd.__init__(self, name='OffsetSequence', **kwargs)
        self.pause = pause
        self.resume = resume

    def time_paused(self):
        if self.pause and self.resume:
            return self.resume.start_time - self.pause.end_time
        else:
            return 0

    

class ExposureSequence(ArbCmd):
    '''A scientific exposure between Resume and Pause'''

    def __init__(self, resume, pause, **kwargs):
        ArbCmd.__init__(self, name='ExposureSequence', **kwargs)
        self.pause = pause
        self.resume = resume

    def time_exposing(self):
        if self.pause and self.resume:
            return self.pause.start_time - self.resume.end_time
        else:
            return 0

    

class CompleteObs(ArbCmd):

    def __init__(self, *args, **kwargs):
        ArbCmd.__init__(self, *args, **kwargs)
        self.cmds = []

    def total_time(self):
        '''Total observation time from start of PresetAO to end of StopAO'''
        if self.end_time is None or self.start_time is None:
            return 0
        return self.end_time - self.start_time

    def total_open_time(self):
        '''Total time available from instrument'''
        return self.total_time() - self.setup_duration() - self.offsets_overhead()

    def setup_duration(self):
        '''Total setup time from start of PresetAO to end of StartAO'''
        startao = list(filter(lambda x: x.name in ['StartAO', 'Start AO'], self.cmds))[0]
        return startao.end_time - self.start_time

    def ao_setup_overhead(self):
        '''Total AO time from start of PresetAO to end of StartAO'''
        ao_time = 0
        is_intervention = False
        for cmd in self.cmds:
            if cmd.name in 'CenterStar CenterPupils CheckFlux CloseLoop'.split():
                is_intervention = True

        for cmd in self.cmds:
            if cmd.name in 'Acquire Done'.split():
                continue
            if cmd.end_time is None or cmd.start_time is None:
                continue
            if is_intervention and cmd.name == 'AcquireRefAO': # Avoid double counting acquisition commands
                continue

            if cmd.end_time is not None and cmd.start_time is not None:
                this_cmd_time = cmd.end_time - cmd.start_time
                ao_time += this_cmd_time
            if cmd.name in ['StartAO', 'Start AO']:
                return ao_time
        return 0

    def telescope_overhead(self):
        '''Telescope overhead during setup time'''
        return self.setup_duration() - self.ao_setup_overhead()

    def offsets_overhead(self):
        '''Time spent executing offsets'''
        offsets_time = 0
        for cmd in self.cmds:
            if cmd.name in ['PauseAO', 'Pause']:
                pause_time = cmd.start_time
            if cmd.name in ['ResumeAO', 'Resume']:
                resume_time = cmd.end_time
                offsets_time += resume_time - pause_time
        return offsets_time

    def total_ao_overhead(self):
        '''Time spent executing AO commands'''
        return self.ao_setup_overhead() + self.offsets_overhead()


def detectCompleteObs(cmds):
    '''
    Detect a complete observations series: PresetAO, Acquire,
    StarAO, and Stop.
    '''

    inObs = False
    inPreset = False
    newCmds = []
    for cmd in cmds:
        if cmd.name == 'PresetAO' and cmd.is_instrument_preset():
            inPreset = True
            inObs = False
            obsCmd = CompleteObs(name='CompleteObs', start_time = cmd.start_time)
            obsCmd.wfs = cmd.wfs
            obsCmd.mode = cmd.mode
            obsCmd.mag = cmd.mag
            obsCmd.cmds.append(cmd)

        elif inPreset is True and cmd.name == 'Cancel':
            inPreset = False
            inObs = False

        elif inObs is True:
            obsCmd.cmds.append(cmd)
            if cmd.name in ['Stop', 'StopAO']:
                obsCmd.end_time = cmd.end_time
                # If we are here, we got a PresetAO, a startAO and a stopAO
                # so we declare it a success
                obsCmd.success = True
                newCmds.append(obsCmd)
                inObs = False

        elif inPreset is True:
            obsCmd.cmds.append(cmd)
            if cmd.name in ['StartAO', 'Start AO']:
                inPreset = False
                inObs = True

        newCmds.append(cmd)

    return newCmds


def detectAcquires(cmds):
    '''
    Detect sequences of AcquireRefAO and subcommands and group them
    into a meta 'Acquire' command
    '''
    newCmds = []
    inAcquire = False
    for cmd in cmds:
        if cmd.name == 'AcquireRefAO':
            inAcquire = True
            acquireCmd = ArbCmd(name='Acquire', start_time=cmd.start_time)
            acquireCmd.success = cmd.success
            acquireCmd.errstr = cmd.errstr
            acquireDone = False

        elif inAcquire is True:

            if cmd.name == 'CheckFlux':
                acquireCmd.success = acquireCmd.success and cmd.success
                acquireCmd.errstr += cmd.errstr
                acquireCmd.end_time = cmd.end_time
                if hasattr(cmd, 'estimatedMag'):
                    acquireCmd.estimatedMag = cmd.estimatedMag
                if hasattr(cmd, 'hoBinning'):
                    acquireCmd.hoBinning = cmd.hoBinning
                if hasattr(cmd, 'hoSpeed'):
                    acquireCmd.hoSpeed = cmd.hoSpeed
                
            elif cmd.name in \
               ['CenterPupils', 'CenterStar', 'CloseLoop', 'OptimizeGain',
                'ReCloseLoop', 'getLastImage', 'ApplyOpticalGain']:
                acquireCmd.success = acquireCmd.success and cmd.success
                acquireCmd.errstr += cmd.errstr
                acquireCmd.end_time = cmd.end_time
 
            elif cmd.name == 'Done':
                acquireCmd.success = acquireCmd.success and cmd.success
                acquireCmd.errstr += cmd.errstr
                acquireCmd.end_time = cmd.end_time
                acquireDone = True

                if cmd.success==True:
                    newCmds.append(acquireCmd)
                    inAcquire=False

            else:
                if not acquireDone:
                    acquireCmd.success = False
                    acquireCmd.errstr += ' Command not completed'
                    newCmds.append( acquireCmd)
                    inAcquire = False

        newCmds.append(cmd)

    return newCmds
       

def detectOffsets(cmds):
    '''
    Detect Pause-Offset-Resume sequences and build a meta 'Offset' command
    for each of them.
    '''

    if len(cmds)<3:
        return cmds

    newCmds = []
    for n in range(len(cmds)-2):

        if cmds[n].name == 'Pause' and \
           cmds[n+1].name == 'OffsetXY' and \
           cmds[n+2].name == 'Resume':

           t0 = cmds[n+0].start_time
           t1 = cmds[n+2].end_time
           success = reduce(operator.and_, [x.success for x in cmds[n:n+3]])
           errstr = ' '.join([x.errstr for x in cmds[n:n+3]])
           args = cmds[n+1].args

           cmd = OffsetSequence(cmds[n], cmds[n+2], args=args, start_time=t0, end_time=t1, success=success, errstr=errstr)
           newCmds.append(cmd) 

        elif cmds[n].name == 'Pause' and \
           cmds[n+1].name == 'OffsetXY':
           # Last command is not a Resume

           t0 = cmds[n+0].start_time
           t1 = cmds[n+1].end_time
           success = reduce(operator.and_, [x.success is True for x in cmds[n:n+2]])
           errstr = ' '.join([x.errstr for x in cmds[n:n+2]])
           args = cmds[n+1].args

           if success:
               success = False
               errstr = 'Resume was not sent'

           cmd = OffsetSequence(cmds[n], None, args=args, start_time=t0, end_time=t1, success=success, errstr=errstr)
           newCmds.append(cmd) 

        elif cmds[n].name == 'Pause' and \
             cmds[n+1].name == 'Resume':

           t0 = cmds[n].start_time
           t1 = cmds[n+1].end_time
           success = cmds[n].success and cmds[n+1].success
           errstr = cmds[n].errstr + ' ' + cmds[n+1].errstr
           args = ''

           cmd = OffsetSequence(cmds[n], cmds[n+1], args=args, start_time=t0, end_time=t1, success=success, errstr=errstr)
           newCmds.append(cmd) 

        elif cmds[n].name == 'Pause':
           # Next command is not an OffsetXY nor a Resume

           t0 = cmds[n].start_time
           t1 = cmds[n].end_time
           success = cmds[n].success
           errstr = cmds[n].errstr
           args = ''

           if success:
               success = False
               errstr = 'no OffsetXY or Resume'

           cmd = OffsetSequence(cmds[n], None, args=args, start_time=t0, end_time=t1, success=success, errstr=errstr)
           newCmds.append(cmd) 

        elif cmds[n].name == 'Resume' and \
             cmds[n+1].name == 'Pause':

           t0 = cmds[n].start_time
           t1 = cmds[n].end_time
           success = cmds[n].success and cmds[n+1].success
           errstr = cmds[n].errstr + ' ' + cmds[n+1].errstr

           cmd = ExposureSequence(cmds[n], cmds[n+1], args=args, start_time=t0, end_time=t1, success=success, errstr=errstr)
           newCmds.append(cmd) 

        newCmds.append(cmds[n])

    return newCmds


def cmdsByName(cmds, name):
    return filter(lambda x: x.name == name, cmds)


def outputEvents(title, events, sort=True, complete_list=None):

    if sort:
        ev = {}
        sortedEvents = []
        for e in events:
            ev[e.t] = e
        for k in sorted(ev.keys()):
            sortedEvents.append(ev[k])
    else:
        sortedEvents = events

    if not args.html:
        print()
        print(title)

        print('Total: %d' % len(sortedEvents))
        for e in sortedEvents:
             print('%s %s %s' % (timeStr(e.t), e.name, e.details))

    else:
        print('<HR>')
        print('<H2>%s</H2>' % title)
        print('<p>Total: %d</p>' % len(sortedEvents))
        if len(sortedEvents)>0:
            print('<table id="aotable">')
            print(sortedEvents[0].htmlHeader())
            for e in sortedEvents:
                print(e.htmlRow())
                if complete_list is not None:
                    complete_list[timeStr(e.t)] = e.htmlRow()
            print('</table>')
        else:
            print('<p>')


def output_cmd(title, found, complete_list=None):

    found = list(found)
    success = len([f for f in found if f.success])
    success_rate = 0
    if len(found)>0:
        success_rate = float(success) / len(found)

    if not args.html:
        print()
        print(title)

        print('Total: %d - Success rate: %d%%' % (len(found), int(success_rate*100)))
        for f in found:
             print(f.report())

    else:
        print('<HR>')
        print('<H2>%s</H2>' % title)
        print('<p>Total: %d - Success rate: %d%%</p>' % (len(found), int(success_rate*100)))
        if len(found)>0:
            print('<p>')
            print('<table id="aotable">')
            print('<tr><th>Time</th><th>Command</th><th>Ex. time (s)</th><th style="width: 300px">Result</th><th>Details</th><th>More details</th></tr>')
        for cmd in found:
            strtime = timeStr(cmd.start_time)
            if (cmd.end_time is not None) and (cmd.start_time is not None):
                elapsed = '%5.1f s' % (cmd.end_time - cmd.start_time,)
            else:
                elapsed = 'Unknown'
            if cmd.success is True:
                errstr = 'Success'
            else:
                errstr = cmd.errorString()
            row = '<tr><td>%s</td><td>%s</td><td>%s</td><td style="width: 300px">%s</td><td>%s</td><td>%s</td></tr>' % \
                  (strtime, cmd.name, elapsed, errstr, '<br>'.join(cmd.details()), '<br>'.join(cmd.details2()))
            print(row)
            if complete_list is not None:
                complete_list[strtime] = row

        if len(found)>0:
            print('</table>\n')
            print('</p>')

    return success_rate


def update_cmd_csv(cmds):

    csvfilename = os.path.join(args.outdir, 'cmd_%s.csv' % args.side)
    # read csv
    if os.path.exists(csvfilename):
        with open(csvfilename, 'r') as csvfile:
            data = list(csv.reader(csvfile, delimiter=','))
    else:
        data = []

    cmds = list(cmds)
    if len(cmds) < 1:
        return

    # Remove anything matching this day/cmd (assumes all cmds are equal)
    data = filter(lambda row: (row[0] != args.day) or (row[2] != cmds[0].name), data)

    # Remove header if any
    data = list(filter(lambda row: row[0] != 'day', data))

    # Add our data
    for cmd in cmds:
        if not cmd.success:
            continue
        if cmd.end_time is None or cmd.start_time is None:
            continue

        d = dayStr(cmd.start_time)
        h = hourStr(cmd.start_time)
        tottime = '%d' % (cmd.end_time - cmd.start_time)
        row = (d, h, cmd.name, tottime)
        data.append(row)

    data.sort(key=lambda x: x[0])

    hdr = ('day', 'hour', 'command', 'elapsed')
    data = [hdr]+data

    # Save csv   
    with open(csvfilename, 'w') as csvfile:
        csv.writer(csvfile, delimiter=',').writerows(data)


def update_output_csv(cmds):

    csvfilename = os.path.join(args.outdir, 'data_%s.csv' % args.side)

    # read csv
    if os.path.exists(csvfilename):
        with open(csvfilename, 'r') as csvfile:
            data = list(csv.reader(csvfile, delimiter=','))
    else:
        data = []

    # Remove anything matching this day
    data = filter(lambda row: row[0] != args.day, data)

    # Remove header if any
    data = list(filter(lambda row: row[0] != 'day', data))

    # Add our data
    for cmd in cmds:
        d = dayStr(cmd.start_time)
        h = hourStr(cmd.start_time)
        tottime = '%d' % cmd.total_time()
        opentime = '%d' % cmd.total_open_time()
        setuptime = '%d' % cmd.setup_duration()
        aosetuptime = '%d' % cmd.ao_setup_overhead()
        telsetuptime = '%d' % cmd.telescope_overhead()
        offsetstime = '%d' % cmd.offsets_overhead()
        tottime_h = str(float(tottime)/3600)
        opentime_h = str(float(opentime)/3600)
         
        row = (d, h, tottime, tottime_h, opentime, opentime_h, setuptime, aosetuptime, telsetuptime, offsetstime, cmd.wfs, cmd.mode, cmd.mag)
        data.append(row)

    data.sort(key=lambda x: x[0])

    hdr = ('day', 'hour', 'time', 'time_h', 'open', 'open_h', 'setup', 'aosetup', 'telsetup', 'offsets', 'wfs', 'mode', 'magnitude')
    data = [hdr]+data

    # Save csv   
    with open(csvfilename, 'w') as csvfile:
        csv.writer(csvfile, delimiter=',').writerows(data)


##################
# Text/html output

if args.html:
    htmltitle = 'AO commands statistics for %s' % args.day
    print('''
<html>
<head>
  <title>%s</title>
  <link rel="stylesheet" href="aotable.css">
</head>
<body>
<H1>%s</H1>
''' % (htmltitle, htmltitle))


AOARB_cmds = get_AOARB_cmds()
AOARB_cmds = detectOffsets(AOARB_cmds)
AOARB_cmds = detectAcquires(AOARB_cmds)
AOARB_cmds = detectCompleteObs(AOARB_cmds)

update_output_csv(cmdsByName(AOARB_cmds, 'CompleteObs'))

update_cmd_csv(cmdsByName(AOARB_cmds, 'PresetAO'))
update_cmd_csv(cmdsByName(AOARB_cmds, 'CenterStar'))
update_cmd_csv(cmdsByName(AOARB_cmds, 'CenterPupils'))
update_cmd_csv(cmdsByName(AOARB_cmds, 'CheckFlux'))
update_cmd_csv(cmdsByName(AOARB_cmds, 'CloseLoop'))
update_cmd_csv(cmdsByName(AOARB_cmds, 'OptimizeGain'))
update_cmd_csv(cmdsByName(AOARB_cmds, 'ApplyOpticalGain'))
update_cmd_csv(cmdsByName(AOARB_cmds, 'OffsetXY'))

table = {}
success = {}

complete_list = {}

################
# Events report

events = []

for name, string, klass in [
        ('AOARB', ' - SkipFrame', SkipFrameEvent),
        ('fastdiagn', 'Failing actuator detected', FailedActuatorEvent),
        ('fastdiagn', 'FUNCTEMERGENCYST', RIPEvent),
        ('housekeeper', 'FUNCTEMERGENCYST', RIPEvent),
        ]:

    found = search(name, string, mindiff=120)
    events += map(klass.fromLogLine, found)

outputEvents('Events', events, sort=True, complete_list=complete_list)



###################
# Commmands summary

for string, title in [
        ('CompleteObs',  'Complete observations (from PresetAO to StopAO, instrument presets only)'),
        ('PresetAO',     'PresetAO'),
        ('Acquire',      'Acquire - StartAO sequences'),
        ('OffsetSequence',       'Pause - Offset - Resume sequences'),
        ('ExposureSequence',     'Resume - Pause sequences'),
        ('AcquireRefAO', 'AcquireRefAO'),
        ('StartAO',      'StartAO'),
        ('CenterStar',   'CenterStar'),
        ('CenterPupils', 'CenterPupils'),
        ('CheckFlux',    'CheckFlux'),
        ('CloseLoop',    'CloseLoop'),
        ('OptimizeGain', 'OptimizeGain'),
        ('ApplyOpticalGain', 'ApplyOpticalGain'),
        ('OffsetXY',     'OffsetXY'),
        ('OffsetZ',      'OffsetZ'),
        ('Pause',        'Pause'),
        ('Resume',       'Resume'),
        ('PowerOnAdSec', 'PowerOnAdSec'),
        ('PresetFlat',   'PresetFlat'),
        ('MirrorRest',   'MirrorRest'),
        ]:

    found = cmdsByName(AOARB_cmds, string)
    output_cmd(title, found, complete_list=complete_list)

print('<HR>')
print('<H2>All logs in temporal order</H2>')
print('<p>')
print('<table id="aotable">')
print('<tr><th>Time</th><th>Command</th><th>Ex. time (s)</th><th style="width: 300px">Result</th><th>Details</th><th>More details</th></tr>')
for k in sorted(complete_list.keys()):
    print(complete_list[k])
print('</table>\n')
print('</p>')


#######

if args.html:
    print('''
</body>
</html>
''')

