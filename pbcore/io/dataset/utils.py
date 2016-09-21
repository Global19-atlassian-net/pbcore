###############################################################################
# Copyright (c) 2011-2016, Pacific Biosciences of California, Inc.
#
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# * Redistributions of source code must retain the above copyright
#   notice, this list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright
#   notice, this list of conditions and the following disclaimer in the
#   documentation and/or other materials provided with the distribution.
# * Neither the name of Pacific Biosciences nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# NO EXPRESS OR IMPLIED LICENSES TO ANY PARTY'S PATENT RIGHTS ARE GRANTED BY
# THIS LICENSE.  THIS SOFTWARE IS PROVIDED BY PACIFIC BIOSCIENCES AND ITS
# CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT
# NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
# PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL PACIFIC BIOSCIENCES OR
# ITS CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
# OR
# BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER
# IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
###############################################################################

# Author: Martin D. Smith


"""
Utils that support fringe DataSet features.
"""
import os
import tempfile
import logging
import json
import shutil
import datetime
import pysam
import numpy as np
from pbcore.util.Process import backticks

log = logging.getLogger(__name__)

def getTimeStampedName(mType):
    """Generate a timestamped name using the given metatype 'mType' and the
    current UTC time"""
    mType = mType.lower()
    mType = '_'.join(mType.split('.'))
    time = datetime.datetime.utcnow().strftime("%y%m%d_%H%M%S%f")[:-3]
    return "{m}-{t}".format(m=mType, t=time)

def which(exe):
    if os.path.exists(exe) and os.access(exe, os.X_OK):
        return exe
    path = os.getenv('PATH')
    for this_path in path.split(os.path.pathsep):
        this_path = os.path.join(this_path, exe)
        if os.path.exists(this_path) and os.access(this_path, os.X_OK):
            return this_path
    return None

def consolidateXml(indset, outbam, useTmp=True, cleanup=True):
    tmpout = tempfile.mkdtemp(suffix="consolidate-xml")
    log.debug("Consolidating to {o}".format(o=outbam))
    tmp_xml = os.path.join(tmpout,
                           "orig.{t}.xml".format(
                               t=indset.__class__.__name__.lower()))

    final_free_space = disk_free(os.path.split(outbam)[0])
    projected_size = sum(file_size(infn)
                         for infn in indset.toExternalFiles())
    log.debug("Projected size:            {p}".format(p=projected_size))
    log.debug("In place free space:       {f}".format(f=final_free_space))
    # give it a 5% buffer
    if final_free_space < (projected_size * 1.05):
        raise RuntimeError("Not enough space available in {o} to consolidate, "
                           "{p} required, {a} available".format(
                               o=os.path.split(outbam)[0],
                               p=projected_size * 1.05,
                               a=final_free_space
                               ))
    if useTmp:
        tmp_free_space = disk_free(tmpout)
        log.debug("Tmp free space (need ~2x): {f}".format(f=tmp_free_space))
        # need 2x for tmp in and out, plus 10% buffer
        if tmp_free_space > (projected_size * 2.1):
            log.debug("Using tmp storage: " + tmpout)
            indset.copyTo(tmp_xml)
            origOutBam = outbam
            outbam = os.path.join(tmpout, "outfile.bam")
        else:
            log.debug("Using in place storage")
            indset.write(tmp_xml)
            useTmp = False
    _pbmergeXML(tmp_xml, outbam)
    if useTmp:
        shutil.copy(outbam, origOutBam)
        shutil.copy(outbam + ".pbi", origOutBam + ".pbi")
        if cleanup:
            shutil.rmtree(tmpout)
    return outbam

def consolidateBams(inFiles, outFile, filterDset=None, useTmp=True):
    """Take a list of infiles, an outFile to produce, and optionally a dataset
    (filters) to provide the definition and content of filtrations."""
    # check space available
    final_free_space = disk_free(os.path.split(outFile)[0])
    projected_size = sum(file_size(infn) for infn in inFiles)
    log.debug("Projected size:            {p}".format(p=projected_size))
    log.debug("In place free space:       {f}".format(f=final_free_space))
    # give it a 5% buffer
    if final_free_space < (projected_size * 1.05):
        raise RuntimeError("No space available to consolidate")
    if useTmp:
        tmpout = tempfile.mkdtemp(suffix="consolidation-filtration")
        tmp_free_space = disk_free(tmpout)
        log.debug("Tmp free space (need ~2x): {f}".format(f=tmp_free_space))
        # need 2x for tmp in and out, plus 10% buffer
        if tmp_free_space > (projected_size * 2.1):
            log.debug("Using tmp storage: " + tmpout)
            tmpInFiles = _tmpFiles(inFiles, tmpout)
            origOutFile = outFile
            origInFiles = inFiles[:]
            inFiles = tmpInFiles
            outFile = os.path.join(tmpout, "outfile.bam")
        else:
            log.debug("Using in place storage")
            useTmp = False

    if filterDset and filterDset.filters:
        finalOutfile = outFile
        outFile = _infixFname(outFile, "_unfiltered")
    _mergeBams(inFiles, outFile)
    if filterDset and filterDset.filters:
        _filterBam(outFile, finalOutfile, filterDset)
        outFile = finalOutfile
    _indexBam(outFile)
    _pbindexBam(outFile)
    if useTmp:
        shutil.copy(outFile, origOutFile)
        shutil.copy(outFile + ".bai", origOutFile + ".bai")
        shutil.copy(outFile + ".pbi", origOutFile + ".pbi")
        # cleanup:
        shutil.rmtree(os.path.split(outFile)[0])

def _tmpFiles(inFiles, tmpout=None):
    tmpInFiles = []
    if tmpout is None:
        tmpout = tempfile.mkdtemp(suffix="consolidation-filtration")
    for i, fname in enumerate(inFiles):
        newfn = _infixFname(os.path.join(tmpout, os.path.basename(fname)), i)
        shutil.copy(fname, newfn)
        tmpInFiles.append(newfn)
    return tmpInFiles

def disk_free(path):
    if path == '':
        path = os.getcwd()
    space = os.statvfs(path)
    return space.f_bavail * space.f_frsize

def file_size(path):
    return os.stat(path).st_size

def _pbindexBam(fname):
    cmd = "pbindex {i}".format(i=fname)
    log.info(cmd)
    o, r, m = backticks(cmd)
    if r != 0:
        raise RuntimeError(m)
    return fname + ".pbi"

# Singleton so we don't need to check and parse repeatedly
class BamtoolsVersion:
    class __BamtoolsVersion:
        def __init__(self):
            cmd = "bamtools -v"
            o, r, m = backticks(cmd)

            if r == 127:
                self.good = False
                return

            version = ''
            for line in o:
                if line.startswith("bamtools"):
                    version = line.split(' ')[-1]
                    break
            self.number = version
            if map(int, version.split('.')) >= [2, 4, 0]:
                self.good = True
            else:
                self.good = False

    instance = None
    def __init__(self):
        if not BamtoolsVersion.instance:
            BamtoolsVersion.instance = BamtoolsVersion.__BamtoolsVersion()

    def __getattr__(self, name):
        return getattr(self.instance, name)

    def check(self):
        if not self.good:
            raise RuntimeError("Bamtools version >= 2.4.0 required for "
                               "consolidation")

def _sortBam(fname):
    BamtoolsVersion().check()
    tmpname = _infixFname(fname, "_sorted")
    cmd = "bamtools sort -in {i} -out {o}".format(i=fname, o=tmpname)
    log.info(cmd)
    o, r, m = backticks(cmd)
    if r != 0:
        raise RuntimeError(m)
    shutil.move(tmpname, fname)

def _indexBam(fname):
    pysam.samtools.index(fname, catch_stdout=False)
    return fname + ".bai"

def _indexFasta(fname):
    pysam.samtools.faidx(fname, catch_stdout=False)
    return fname + ".fai"

def _mergeBams(inFiles, outFile):
    BamtoolsVersion().check()
    if len(inFiles) > 1:
        cmd = "bamtools merge -in {i} -out {o}".format(i=' -in '.join(inFiles),
                                                       o=outFile)
        log.info(cmd)
        o, r, m = backticks(cmd)
        if r != 0:
            raise RuntimeError(m)
    else:
        shutil.copy(inFiles[0], outFile)

def _pbmergeXML(indset, outbam):
    cmd = "pbmerge -o {o} {i} ".format(i=indset,
                                             o=outbam)
    log.info(cmd)
    o, r, m = backticks(cmd)
    if r != 0:
        raise RuntimeError(m)
    return outbam

def _filterBam(inFile, outFile, filterDset):
    BamtoolsVersion().check()
    tmpout = tempfile.mkdtemp(suffix="consolidation-filtration")
    filtScriptName = os.path.join(tmpout, "filtScript.json")
    _emitFilterScript(filterDset, filtScriptName)
    cmd = "bamtools filter -in {i} -out {o} -script {s}".format(
        i=inFile, o=outFile, s=filtScriptName)
    log.info(cmd)
    o, r, m = backticks(cmd)
    if r != 0:
        raise RuntimeError(m)

def _infixFname(fname, infix):
    prefix, extension = os.path.splitext(fname)
    return prefix + str(infix) + extension

def _earlyInfixFname(fname, infix):
    path, name = os.path.split(fname)
    tokens = name.split('.')
    tokens.insert(1, str(infix))
    return os.path.join(path, '.'.join(tokens))

def _swapPath(dest, infile):
    return os.path.join(dest, os.path.split(infile)[1])

def _fileCopy(dest, infile, uuid=None):
    fn = _swapPath(dest, infile)
    if os.path.exists(fn):
        if uuid is None:
            raise IOError("File name exists in destination: "
                          "{f}".format(f=infile))
        subdir = os.path.join(dest, uuid)
        if not os.path.exists(subdir):
            os.mkdir(subdir)
        fn = _swapPath(subdir, fn)
    shutil.copy(infile, fn)
    assert os.path.exists(fn)
    return fn


def _emitFilterScript(filterDset, filtScriptName):
    """Use the filter script feature of bamtools. Use with specific filters if
    all that are needed are available, otherwise filter by readname (easy but
    uselessly expensive)"""
    filterMap = {'rname': 'reference',
                 'pos': 'position',
                 'length': 'queryBases',
                 'qname': 'name',
                 'rq': 'mapQuality'}
    cheapFilters = True
    for filt in filterDset.filters:
        for req in filt:
            if not filterMap.get(req.name):
                cheapFilters = False
    if cheapFilters:
        script = {"filters":[]}
        for filt in filterDset.filters:
            filtDict = {}
            for req in filt:
                name = filterMap[req.name]
                if name == 'reference':
                    if req.operator == '=' or req.operator == '==':
                        filtDict[name] = req.value
                    else:
                        raise NotImplementedError()
                else:
                    filtDict[name] = req.operator + req.value
            script['filters'].append(filtDict)
    else:
        names = [rec.qName for rec in filterDset]
        script = {"filters":[{"name": name} for name in names]}
    with open(filtScriptName, 'w') as scriptFile:
        scriptFile.write(json.dumps(script))

RS = 65536

def xy_to_hn(x, y):
    return x * RS + y

def hn_to_xy(hn):
    x = hn/RS
    y = hn - (x * RS)
    return x, y

def shift(cx, cy, d):
    # 0 is up, 1 is right, 2 is down, 3 is left
    if d == 0:
        cy += 1
    elif d == 1:
        cx += 1
    elif d == 2:
        cy -= 1
    elif d == 3:
        cx -= 1
    return cx, cy, d

def change_d(d):
    d += 1
    d %= 4
    return d

def move(cx, cy, x, y, d):
    if cx == x and cy == y:
        return cx - 1, y, 0
    if abs(x - cx) == abs(y - cy):
        d = change_d(d)
        # expand the search
        if d == 0:
            cx -= 1
        return shift(cx, cy, d)
    else:
        return shift(cx, cy, d)

def find_closest(x, y, pos, limit=81):
    found = False
    cx = x
    cy = y
    d = None
    fails = 0
    while not found:
        hn = xy_to_hn(cx, cy)
        if hn in pos:
            return hn
        else:
            fails += 1
            cx, cy, d = move(cx, cy, x, y, d)
        if fails >= limit:
            return None

def quadratic_expand(lol):
    samples = [[p] for p in lol[0]]
    for ps in lol[1:]:
        newsamples = []
        for p in ps:
            for s in samples:
                newsamples.append(s[:] + [p])
        samples = newsamples
    return samples

def prodround(values, target):
    """Round the floats in values (whose product is <target>) to integers in a
    way that minimizes the absolute change in values
    Args:
        values: a list of numbers
        target: the product of values (perhaps approximate)
    Returns:
        The values array, rounded to integers
    """
    opts = [[np.floor(v), round(v), np.ceil(v)] for v in values]
    combos = quadratic_expand(opts)
    best = combos[0]
    for combo in combos[1:]:
        p = np.prod(combo)
        err = abs(target - p)
        berr = abs(target - np.prod(best))
        rnd = np.sum([abs(v-c) for v, c in zip(values, combo)])
        brnd = np.sum([abs(v-c) for v, c in zip(values, best)])
        if (err < berr) or ((err == berr) and (rnd < brnd)):
            best = combo
    return best

def sampleUniformly(nsamples, dimbounds):
    """dimbounds is list of tuples of range, inclusive"""
    volume = 1
    for dmin, dmax in dimbounds:
        volume *= dmax - dmin
    volume_per_sample = np.true_divide(volume, nsamples)
    sample_side_length = np.power(volume_per_sample,
                                  np.true_divide(1.0, len(dimbounds)))
    per_axis = [max(1.0, np.true_divide((dmax - dmin), sample_side_length))
                for dmin, dmax in dimbounds]
    per_axis = prodround(per_axis, nsamples)
    # Shrink the stride to account for end margins
    strides = [np.true_divide(dmax - dmin, nsam + 1)
               for (dmin, dmax), nsam in zip(dimbounds, per_axis)]
    # introduce a margin
    points = [np.linspace(dmin + dstride,
                          dmax - dstride,
                          round(nsamp))
              for (dmin, dmax), nsamp, dstride in zip(dimbounds, per_axis,
                                                      strides)]
    points = [map(round, ps) for ps in points]
    points = [map(int, ps) for ps in points]
    samples = quadratic_expand(points)
    return samples


def sampleHolesUniformly(nsamples, samplefrom, faillimit=25, rowstart=64,
                         colstart=64, nrows=1024, ncols=1144):
    xys = sampleUniformly(nsamples, [(colstart, ncols), (rowstart, nrows)])
    hns = [find_closest(x, y, samplefrom, limit=faillimit) for x, y in xys]
    return [hn for hn in hns if not hn is None]


