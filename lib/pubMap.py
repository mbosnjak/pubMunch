import sys, logging, optparse, os, collections, tempfile,\
    shutil, glob, array, codecs, string, re, gzip, time, socket, subprocess

import maxRun, pubStore, pubConf, pubGeneric, maxCommon, bigBlat, pubAlg, unidecode
import maxbio, tabfile, maxMysql, maxTables, util, pubMapProp, pubCdr3Filter
from collections import defaultdict
from os.path import *

progFile = os.path.abspath(sys.argv[0])

# a bed12 feature with one added field
BedxClass = collections.namedtuple("bedx", \
    ["chrom", "start", "end", "articleId", "score", "strand", "thickStart",
    "thickEnd", "itemRgb", "blockCount", "blockSizes", "blockStarts", "tSeqTypes"])

# these are needed almost everywhere
dataset = None # e.g. pmc
baseDir = None # e.g. /hive/data/inside/pubs/pmc

def countUpcaseWords(runner, baseDir, wordCountBase, textDir, updateIds):
    " submit map-reduce-style job to count uppercase words if we don't already have a list"
    mapTmpDir = join(baseDir, "mapReduceTmp")
    if isdir(mapTmpDir):
        logging.info("Deleting old directory %s" % mapTmpDir)
        shutil.rmtree(mapTmpDir)

    wordFile = join(baseDir, wordCountBase) # updates use the baseline word file
    if not isfile(wordFile): # if baseline has no wordfile, recreate it
        logging.info("Counting upcase words for protein search to %s" % wordFile)
        pubAlg.mapReduce("protSearch.py:UpcaseCounter", textDir, {}, wordFile, \
            tmpDir=mapTmpDir, updateIds=updateIds, runTest=False, runner=runner)
    else:
        logging.info("Not counting words, file %s found" % wordFile)

    return wordFile

def runStepRange(d, allSteps, fromStep, toStep, args, options):
    """ run a range of steps, from-to, given the list of all steps
    Always skip the debugging steps
    """

    if fromStep not in allSteps:
        logging.error("%s is not a valid command" % fromStep)
        sys.exit(0)
    if toStep not in allSteps:
        logging.error("%s is not a valid command" % toStep)
        sys.exit(0)

    startIdx = allSteps.index(fromStep)
    endIdx   = allSteps.index(toStep)
    nowSteps = allSteps[startIdx:endIdx+1]
    nowSteps = [x for x in nowSteps if not x.endswith("Dbg")]

    logging.info("Running steps %s " % (nowSteps))
    for stepName in nowSteps:
        logging.info("=== RUNNING STEP %s ===" % stepName)
        runStep(d.dataset, stepName, d, options)

def appendAsFasta(inFilename, outObjects, maxSizes, seqLenCutoff, forceDbs=None, isProt=False):
    """ create <db>.<long|short>.fa files in faDir and fill them with data from
    tab-sep inFile (output file from pubRun)

    if forceDbs is a comma-sep string: do not try to infer target dbs from "dbs" field of
    seq table but instead search all sequences against all dbs in the list
    of dbs
    """
    logging.debug("Parsing sequences from %s" % inFilename)

    for row in maxCommon.iterTsvRows(inFilename):
        if forceDbs!=None:
            dbs = forceDbs
        else:
            dbs = row.dbs
            if dbs=="":
                dbs = pubConf.defaultGenomes
            else:
                dbs = dbs.split(',')
                dbs.extend(pubConf.alwaysUseGenomes)

        for db in dbs:
            seq = row.seq
            if isProt and (row.prefixFilterAccept!="Y" or row.suffixFilterAccept!="Y"):
                logging.debug("Skipping seq %s, did not pass prefix/suffix filter" % seq)
                continue
            annotId = int(row.annotId)
            fileId = annotId / (10**pubConf.ANNOTDIGITS)
            articleId = fileId / (10**pubConf.FILEDIGITS)

            if len(seq)<seqLenCutoff:
                dbType = "short"
            else:
                dbType = "long"
            maxSize = maxSizes[dbType]
            outObj = outObjects[db][dbType]
            # if exceeded maxSize and at fileId-boundary
            # start a new output file
            if outObj.nuclCount > maxSize and articleId!=outObj.lastArticleId:
                outObj.file.close()
                outObj.count+=1
                newFname = join(outObj.dir, db+".%.2d.fa" % outObj.count)
                logging.debug("max size reached for %s, creating new file %s" % (db, newFname))
                outObj.file = open(newFname, "w")
                outObj.nuclCount=0
            faId = str(annotId)
            outObj.file.write(">%s\n%s\n"% (faId, seq))
            outObj.nuclCount+=len(seq)
            outObj.lastArticleId=articleId
    
def closeOutFiles(outDict):
    for typeDict in outDict.values():
        for fileObject in typeDict.values():
            logging.debug("Closing %s" % fileObject.file.name)
            fileObject.file.close()

class Object():
    pass

def createOutFiles(faDir, dbList, maxSizes):
    """ create one output file per db
        return dict[db] => dict[dbType => Object with attributes:
        file, count, dir, nuclCount
    """
    outDict = {}
    for db in dbList:
        for dbType in maxSizes:
            dbDir = join(faDir, dbType)
            if not isdir(dbDir):
                os.makedirs(dbDir)
            filename = join(dbDir, db+".00.fa")
            fh = open(filename, "w")
            logging.debug("Created file %s" % filename)
            outDict.setdefault(db, {})
            dbOut = Object()
            dbOut.dir = dbDir
            dbOut.count = 0
            dbOut.nuclCount = 0
            dbOut.file = fh
            dbOut.lastArticleId = 0
            outDict[db][dbType]= dbOut
    return outDict
        
def pubToFasta(inDir, outDir, dbList, maxSizes, seqLenCutoff, forceDbs=None, isProt=False):
    """ convert sequences from tab format to fasta, 
        distribute over species: create one fa per db 
    """
    maxCommon.mustBeEmptyDir(outDir, makeDir=True)
    logging.info("Converting tab files in %s to fasta in %s" % (inDir, outDir))
    inFiles = glob.glob(join(inDir, "*.tab"))
    outFileObjects = createOutFiles(outDir, dbList, maxSizes)
    pm = maxCommon.ProgressMeter(len(inFiles))
    logging.debug("Running on %d input files" % len(inFiles))
    inCountFiles = list(enumerate(inFiles))
    if len(inCountFiles)==0:
        raise Exception("no input files in dir %s" % inDir)
    for count, inFile in inCountFiles:
        logging.debug("parsing %d of %d input files" % (count, len(inFiles)))
        appendAsFasta(inFile, outFileObjects, maxSizes, seqLenCutoff, forceDbs=forceDbs, isProt=isProt)
        pm.taskCompleted()
    closeOutFiles(outFileObjects)

def indexFilesByTypeDb(faDir, blatOptions):
    """ do a find on dir and sort files by type and db into double-dict 
    e.g. dbFaFiles["short"]["hg19"] = list of hg19 fa files 
    """
    dbFaFiles = {}
    fCount = 0
    for seqType in blatOptions:
        seqTypeDir = join(faDir, seqType)
        faFiles = glob.glob(join(seqTypeDir, "*.fa"))
        logging.debug("%d fa files found in dir %s" % (len(faFiles), seqTypeDir))
        dbFaFiles[seqType]={}
        for faName in faFiles:
            if getsize(faName)==0:
                continue
            fCount += 1 
            db = basename(faName).split(".")[0]
            dbFaFiles[seqType].setdefault(db, [])
            dbFaFiles[seqType][db].append(faName)
    #assert(fCount>0)
    if fCount==0:
        raise Exception("no files found in %s" % faDir)
    return dbFaFiles

def submitBlatJobs(runner, faDir, pslDir, onlyDbs, cdnaDir=None, \
        blatOptions=pubConf.seqTypeOptions, noOocFile=False):
    """ read .fa files from faDir and submit blat jobs that write to pslDir 
        dbs are taken from pubConf, but can be restricted with onlyDbs 
    """
    #maxCommon.makedirs(pslDir)
    maxCommon.mustBeEmptyDir(pslDir, makeDir=True)
    splitParams = pubConf.genomeSplitParams

    # shred genomes/cdna and blat fasta onto these
    dbFaFiles = indexFilesByTypeDb(faDir, blatOptions)
    for seqType, dbFiles in dbFaFiles.iteritems():
        for db, faNames in dbFiles.iteritems():
            if (onlyDbs!=None and len(onlyDbs)!=0) and db not in onlyDbs:
                continue
            logging.debug("seqtype %s, db %s, query file file count %d" % (seqType, db, len(faNames)))
            blatOpt, filterOpt = blatOptions[seqType]
            #pslTypeDir = maxCommon.joinMkdir(pslDir, seqType, db)
            pslTypeDir = maxCommon.joinMkdir(pslDir, db, seqType)
            logging.info("creating blat jobs: db %s, query count %d, output to %s" \
                % (db, len(faNames), pslDir))
            # in cdna mode, we lookup our own 2bit files
            if cdnaDir:
                targetMask =join(cdnaDir, db, "*.2bit")
                targets = glob.glob(targetMask)
                logging.info("Found %s files matching %s" % (len(targets), targetMask))
                if len(targets)==0:
                    logging.warn("Skipping db %s, no target cdna file found" % (db))
                    continue
                splitTarget = False
            # for non ucsc genomes, we also need to find the 2bit file
            elif db.startswith("nonUcsc"):
                dbName = db.replace("nonUcsc_", "")
                dbPath = join(pubConf.nonUcscGenomesDir, dbName+".2bit")
                targets = [dbPath]
                splitTarget = True
            # for UCSC genome DBs: use genbank.conf parameters
            else:
                targets = [db]
                splitTarget = True

            jobLines = list(bigBlat.getJoblines(targets, faNames, pslTypeDir, \
                splitParams, splitTarget, blatOpt, filterOpt, noOocFile=noOocFile))

            logging.info("Scheduling %d jobs" % len(jobLines))
            for line in jobLines:
                runner.submit(line)

def clusterCmdLine(method, inFname, outFname, checkIn=True, checkOut=True):
    """ generate a cmdLine for batch system that calls this module
    with the given parameters
    """
    if checkIn:
        inFname = "{check in exists %s}" % inFname
    if checkOut:
        outFname = "{check out exists %s}" % outFname

    cmd = "%s %s %s %s %s" % (sys.executable, __file__, method, inFname, outFname)
    return cmd

def getJobScript(name):
    " return full path to cluster job script "
    dir = dirname(__file__)
    return join(dir, "jobScripts", name)

def submitSortPslJobs(runner, seqType, inDir, outDir, dbList):
    """ submit jobs to sort psl files, one for each db"""
    logging.info("Sorting psls, mapping to genome coord system and prefixing with db")
    maxCommon.makedirs(outDir, quiet=True)
    jobScript = getJobScript("mapSortFilterPsl")
    for db in dbList:
        dbInDir = join(inDir, db)
        maxCommon.makedirs(outDir, quiet=True)
        dbOutFile = join(outDir, db+".psl")
        cmd = jobScript + " %(dbInDir)s %(dbOutFile)s %(db)s %(seqType)s" % locals()
        if seqType in ["c", "p"]:
            cmd += " --cdnaDir " + pubConf.cdnaDir
        runner.submit(cmd)
    #logging.info("If batch went through: output can be found in %s" % dbOutFile)
        
def concatFiles(inFnames, outFile, cutFields=None):
    # concat all files into some file
    #usfh = open(unsortedPslFname, "w")
    for fn in inFnames:
        outFile.write(open(fn).read())
    outFile.flush() # cannot do close, otherwise temp file will get deleted
    logging.debug("Concatenated %d files to %s" % (len(inFnames), outFile.name))

def makeBlockSizes(pslList):
    """ generate bed block sizes for a bed from 
    potentially overlapping psls. Uses a sort of bitmask.
    """
    # generate bitmask of occupied positions
    pslList.sort(key=lambda f: f.tStart) # sort fts by start pos
    minStart = min([f.tStart for f in pslList])
    maxEnd = max([f.tEnd for f in pslList])
    logging.log(5, "Creating blockSizes for %d psls, length %d" % (len(pslList), maxEnd-minStart))
    for psl in pslList:
        logging.log(5, "psl: %s" % str(psl))
    mask = array.array("b", [0]*(maxEnd-minStart))

    for psl in pslList:
        starts = psl.tStarts.strip(",").split(",")
        sizes = psl.blockSizes.strip(",").split(",")
        for start, size in zip(starts, sizes):
            size = int(size)
            start = int(start) - minStart
            for pos in range(start, start+size):
                mask[pos] = 1

    blockStarts = []
    blockSizes = []
    # search for consec stretches of 1s
    lastStart=None
    wasZero=True
    for i in range(0, len(mask)):
        if mask[i]==1 and wasZero:
            blockStarts.append(i)
            wasZero=False
            lastStart=i
        if mask[i]==0 and not wasZero:
            blockSizes.append(i-lastStart)
            wasZero=True
            lastStart=None
    if lastStart!=None:
        blockSizes.append(len(mask)-lastStart)
    assert(mask[len(mask)-1]==1)
    blockStarts = [str(x) for x in blockStarts]
    blockSizes = [str(x) for x in blockSizes]
    blockSizesCount = mask.count(1)
    return blockStarts, blockSizes, blockSizesCount

def pslListToBedx(chain, minCover):
    """ create bedx feature and check if chain is long enough """
    logging.log(5, "Converting chain with %d psls to bedx" % (len(chain)))
    blockStarts, blockSizes, blockSizeSum = makeBlockSizes(chain)
    if blockSizeSum>=minCover:
        bedNames = []
        chrom = None
        start, end = 99999999999, 0
        tSeqTypes = set()
        for psl in chain:
            name = "%s:%d-%d" % (psl.qName, psl.qStart, psl.qEnd)
            bedNames.append(name)
            db, chrom, tSeqType = psl.tName.split(",")
            tSeqTypes.add(tSeqType)
            start = min(start, psl.tStart)
            end = max(end, psl.tEnd)
        bedName = ",".join(bedNames)
        bedx = BedxClass(chrom, start, end, bedName, blockSizeSum, "+", start, end, "128,128,128", len(blockSizes), ",".join(blockSizes), ",".join(blockStarts), ",".join(tSeqTypes))
        logging.log(5, "final chain %s" % str(bedx))
        return bedx
    else:
        logging.log(5, "chain not long enough, skipping featureList, blockSizeSum is %d" % (blockSizeSum))
        for psl in chain:
            logging.log(5, "skipped psl: %s" % str(psl))
        return None

def indexByDbChrom(pslList):
    " given a list of psls, return a dict (db, chrom) -> pslList "
    pslDict = {}
    for psl in pslList:
        target = psl.tName
        db, chrom, seqType = target.split(",")
        pslDict.setdefault( (db, chrom), [] )
        pslDict[ (db, chrom) ].append(psl)
    return pslDict


def chainPsls(pslList, maxDistDict):
    """ chain features if same chrom, same articleId and closer than maxDist

        chains a query sequence only once and ignores all matches for the same chain
        return a dict chainId -> seqId -> list of psls
    """
    logging.log(5, "%d unchained genome hits" % len(pslList))
    chromPsls = indexByDbChrom(pslList)

    chains = {}
    for dbChrom, chromPslList in chromPsls.iteritems():
        db, chrom = dbChrom
        logging.log(5, "db %s, chrom %s, %d features" % (db, chrom, len(chromPslList)))
        if "_hap" in chrom:
            logging.log(5, "haplotype chromosome, skipping all features")
            continue
        chromPslList = maxbio.sortList(chromPslList, "tStart", reverse=False)
        chain = []
        lastEnd = None
        alreadyChained = {}
        maxDist = maxDistDict.get(db, maxDistDict["default"])
        # chain features
        for psl in chromPslList:
            if psl.qName in alreadyChained:
                oldPsl = alreadyChained[psl.qName]
                if psl.tStart==oldPsl.tStart and psl.tEnd==oldPsl.tEnd and \
                    psl.blockSizes==oldPsl.blockSizes and psl.tName != oldPsl.tName:
                    logging.log(5, "same match, but different tSequenceType (cdna, prot, genome)," \
                        "keeping hit")
                else:
                    logging.log(5, "weird match, q-sequence already in this chain, skipping %s" % \
                        str(psl))
                    continue
            if len(chain)>0 and abs(int(psl.tStart) - lastEnd) > maxDist:
                chainId = chain[0].tName + "-" + str(chain[0].tStart)
                chains[chainId]=chain
                alreadyChained = {}
                chain = []
            logging.log(5, "Adding feature %s to chain" % str(psl))
            chain.append(psl)
            alreadyChained[psl.qName] = psl
            lastEnd = psl.tEnd
        chainId = db + "," + chrom + "-" + str(chain[0].tStart)
        chains[chainId]=chain

    # index all chains by qName to create a nested dict chainId -> seqId -> pslList
    # chainId looks like hg19,chr1,123456
    idxChains = {}
    for chainId, pslList in chains.iteritems():
        pslDict = {}
        for psl in pslList:
            pslDict.setdefault(psl.qName, []).append(psl)
        idxChains[chainId] = pslDict

    return idxChains

#def indexByDb(pslList):
    #""" index psl by db (in target name) and return as dict[db] -> list of psls
        #remove the db from the psl tName field """
    #pslByDb = {}
    #for psl in pslList:
        #db, chrom = psl.tName.split(",")
        #psl = psl._replace(tName=chrom)
        #pslByDb.setdefault(db, []).append(psl)
    #return pslByDb

def getBestElements(dict):
    """ given a dict with name -> score, keep only the ones with the highest score,
        return them as a list
    """ 
    maxScore = max(dict.values())
    result = []
    for key, value in dict.iteritems():
        if value == maxScore:
            result.append(key)
    return result

def onlyLongestChains(chains): 
    """ given a dict chainId -> annotId -> list of psls,
    return a filtered list where members are 
    mapped only to those chains with the most members. 

    e.g. we have four sequences blatted onto genomes.
    These are joined into three chains of hits.
    Chains are specified by their ids and members:
    chain1 -> (s1, s3)
    chain2 -> (s1, s2, s3)
    chain3 -> (s1, s3)
    chain4 -> (s1, s4)

    can be rewritten as
    s1 -> (chain1, chain2, chain3)
    s2 -> (chain1, chain2)
    s3 -> (chain2, chain3)
    s4 -> (chain4, chain2)

    then the weights for the chains are:
    chain1: 2
    chain2: 3
    chain3: 2
    chain4: 2

    so chain2 is kept, its sequences removed from all other chains and the process repeats
    until there are no chains left.
    """

    bestChains = {}
    while len(chains)!=0:
        # create score for chains: number of qNames, e.g. chain1->1, chain2->3
        logging.log(5, "Starting chain balancing with %d chains" % len(chains))
        chainScores = {}
        for chainId, qNameDict in chains.iteritems():
            chainScores[chainId] = len(qNameDict.keys())
        logging.log(5, "chainScores are: %s" % chainScores)

        # keep only chains with best scores, create list with their chainIds
        # and create list with  qNames of all their members
        bestChainIds = getBestElements(chainScores)
        logging.log(5, "Best chainIds are: %s" % str(bestChainIds))
        chainQNames = set()
        for bestChainId in bestChainIds:
            db = bestChainId.split(",")[0]
            bestChain = chains[bestChainId]
            bestChains.setdefault(db, []).append(maxbio.flattenValues(bestChain))
            for pslList in bestChain.values():
                for psl in pslList:
                    chainQNames.add(psl.qName)
        logging.log(5, "Best chain contains %d sequences, removing these from other chains" % \
            len(chainQNames))

        # keep only psls with names not in chainQNames 
        newChains = {}
        for chainId, chainDict in chains.iteritems():
            newChainDict = {}
            for qName, pslList in chainDict.iteritems():
                if qName not in chainQNames:
                    newChainDict[qName]=pslList
            if len(newChainDict)!=0:
                newChains[chainId] = newChainDict
        chains = newChains
    return bestChains

def chainsToBeds(chains):
    """ convert psl chains to lists of bed features 
    Return None if too many features on any db
    Return dict db -> tuple (list of chain-beds, list of all psls for beds) otherwise
    """
    dbBeds = {}
    for db, chains in chains.iteritems():
        logging.debug("Converting %d chains on db %s to bedx" % (len(chains), db))
        dbPsls = []
        # convert all chains to bedx, filtering out chains with too many features
        bedxList = []
        for pslList in chains:
            bedx = pslListToBedx(pslList, pubConf.minChainCoverage)
            if bedx==None:
                continue
            if bedx.end - bedx.start > pubConf.maxChainLength:
                logging.log(5, "Chain %s is too long, >%d" % (bedx, pubConf.maxChainLength))
                continue
            bedxList.append(bedx)
            dbPsls.extend(pslList)

        if len(bedxList)==0:
            logging.log(5, "No bedx for db %s" % db)
            continue
        elif len(bedxList) > pubConf.maxFeatures:
            logging.warn("Too many features on db %s, skipping this article" % db)
            return None
        else:
            dbBeds[db] = (bedxList, dbPsls)
    return dbBeds


def writePslsFuseOverlaps(pslList, outFh):
    """ index psls to their seqTypes. Remove identical psls (due to genome+cdna blatting)
    and replace with one feature with seqTypes added as an additional psl field no. 22
    Add an additional psl field no 23 as the articleId.
    Write to outFh.
    """
    pslSeqTypes = {}
    for psl in pslList:
        psl = [str(p) for p in psl]
        tName = psl[13]
        chrom,db,tSeqType = tName.split(",")
        psl[13] = db
        pslLine = "\t".join(psl)
        pslSeqTypes.setdefault(pslLine, set())
        pslSeqTypes[pslLine].add(tSeqType)

    for pslLine, seqTypes in pslSeqTypes.iteritems():
        psl = pslLine.split("\t")
        psl.append("".join(seqTypes))
        outFh.write("\t".join(psl))
        outFh.write("\n")

def chainPslToBed(tmpPslFname, oneOutFile, pipeSep=False, onlyFields=None):
    """ read psls, chain and convert to bed 
    output is spread over many files, one per db, at basename(oneOutFile).<db>.bed

    filtering out:
    - features on db with too many features for one article
    - too long chains

    returns a dict db -> bedFname
    """

    maxDist = pubConf.maxChainDist
    outBaseName = splitext(splitext(oneOutFile)[0])[0]
    logging.info("Parsing %s" % tmpPslFname)
    if pipeSep:
        groupIterator = maxCommon.iterTsvGroups(open(tmpPslFname), format="psl", \
            groupFieldNumber=9, groupFieldSep="|")
    else:
        groupIterator = maxCommon.iterTsvGroups(open(tmpPslFname), format="psl", \
            groupFieldNumber=9, useChars=10)

    outFiles = {} # cache file handles for speed
    dbOutBedNames = {}
    for articleId, pslList in groupIterator:
        logging.info("articleId %s, %d matches" % (articleId, len(pslList)))
        # the filtering happens all here:
        chainDict = chainPsls(pslList, maxDist)
        chains    = onlyLongestChains(chainDict)
        dbBeds    = chainsToBeds(chains)

        if dbBeds!=None:
            for db, bedPslPair in dbBeds.iteritems():
                bedxList, pslList = bedPslPair
                for bedx in bedxList:
                    # lazily open file here, to avoid 0-len files
                    if db not in outFiles:
                        fname = "%s.%s.bed" % (outBaseName, db)
                        pslFname = "%s.%s.psl" % (outBaseName, db)
                        logging.info("db %s, creating file %s and %s" % (db, fname, pslFname))
                        outFiles[db] = open(fname, "w")
                        outFiles[db+"/psl"] = open(pslFname, "w")
                        dbOutBedNames[db] = fname
                    outFile = outFiles[db]

                    # write all bed features
                    logging.log(5,"%d chained matches" % len(bedx))
                    if onlyFields != None:
                        bedx = bedx[:onlyFields]
                    strList = [str(x) for x in bedx]
                    outFile.write("\t".join(strList))
                    outFile.write("\n")
                outPslFile = outFiles[db+"/psl"]
                writePslsFuseOverlaps(pslList, outPslFile)

    # when no match was found on any db, then the file was not created and parasol thinks
    # that the job has crashed. Make Parasol happy by creating a zero-byte outfile 
    if not isfile(oneOutFile):
        logging.info("Creating empty file %s for parasol" % oneOutFile)
        open(oneOutFile, "w").write("")

    return dbOutBedNames
            
def removeEmptyDirs(dirList):
    """ go over dirs and remove those with only empty files, return filtered list """
    filteredList = []
    for dir in dirList:
        fileList = os.listdir(dir)
        isEmpty=True
        for file in fileList:
            path = join(dir, file)
            if os.path.getsize(path) > 0:
                isEmpty = False
                break
        if not isEmpty:
            filteredList.append(dir)
    return filteredList
            
def submitMergeSplitChain(runner, textDir, inDirs, splitDir, bedDir, maxDbMatchCount, dbList, updateIds):
    " join all psl files from each db into one big PSL for all dbs, keep best matches and re-split "
    maxCommon.mustBeEmptyDir(bedDir, makeDir=True)
    maxCommon.mustBeEmptyDir(splitDir, makeDir=True)

    logging.info("Reading psls from directories %s" % inDirs)
    #filteredDirs = removeEmptyDirs(inDirs)
    #if len(filteredDirs)==0:
        #raise Exception("Nothing to do, %s are empty" % inDirs)

    # merge/sort/filter psls into one file and split them again for chaining
    mergedPslFilename = mergeFilterPsls(inDirs)
    articleToChunk = pubGeneric.readArticleChunkAssignment(textDir, updateIds)
    splitPsls(mergedPslFilename, splitDir, articleToChunk, maxDbMatchCount)
    os.remove(mergedPslFilename)

    submitChainFileJobs(runner, splitDir, bedDir, list(dbList))

def mergeFilterPsls(inDirs):
    """ merge/sort/filter all psls (separated by db) in inDir into a temp file with all
    psls for all dbs, split into chunked pieces and write them to outDir 
    """
    tmpFile, tmpPslFname = pubGeneric.makeTempFile("pubMap_split.", suffix=".psl")
    tmpFile.close()
    logging.debug("Merging into tmp file %s" % tmpPslFname)
    pslSortTmpDir = join(pubConf.getTempDir(), "pubMap-sortSplitPsls")
    if isdir(pslSortTmpDir):
        shutil.rmtree(pslSortTmpDir)
    os.makedirs(pslSortTmpDir)
    logging.info("Sorting psls in %s to temp file %s" % (str(inDirs), pslSortTmpDir))
    inDirString = " ".join(inDirs)
    cmd = "pslSort dirs -nohead stdout %(pslSortTmpDir)s %(inDirString)s | pslCDnaFilter stdin %(tmpPslFname)s -minAlnSize=19 -globalNearBest=0" % locals()
    maxCommon.runCommand(cmd)
    shutil.rmtree(pslSortTmpDir)
    return tmpPslFname

def splitPsls(inPslFile, outDir, articleToChunk, maxDbMatchCount):
    " splitting psls according to articleToChunk, ignore articles with > maxDbMatchCount psls "
    logging.info("SPLIT PSL - Reading %s, splitting to directory %s" % (inPslFile, outDir))
    articleDigits = pubConf.ARTICLEDIGITS
    groupIterator = maxCommon.iterTsvGroups(inPslFile, format="psl", groupFieldNumber=9, useChars=articleDigits)

    chunkFiles = {}
    chunkId = 0
    articleIdCount = 0
    for articleId, pslList in groupIterator:
        articleId = int(articleId)
        articleIdCount += 1
        # try to derive the current chunkId from the index files
        # if that doesn't work, just create one chunk for each X
        # articleIds
        if articleToChunk:
            chunkId  = articleToChunk[int(articleId)] / pubConf.chunkDivider
        else:
            chunkId  = articleIdCount / pubConf.chunkArticleCount
        logging.debug("articleId %s, %d matches" % (articleId, len(pslList)))
        if len(pslList) >= maxDbMatchCount:
            logging.debug("Skipping %s: too many total matches" % str(pslList[0].qName))
            continue

        chunkIdStr  = "%.5d" % chunkId
        if not chunkId in chunkFiles:
            chunkFname = join(outDir, chunkIdStr+".psl")
            outFile = open(chunkFname, "w")
            chunkFiles[chunkId]= outFile
        else:
            outFile = chunkFiles[chunkId]

        for psl in pslList:
            pslString = "\t".join([str(x) for x in psl])+"\n"
            outFile.write(pslString)

        articleIdCount += 1
    logging.info("Finished writing to %d files in directory %s" % (len(chunkFiles), outDir))

def submitChainFileJobs(runner, pslDir, bedDir, dbList):
    """ submit jobs, one for each psl file in pslDir, to chain psls and convert to bed """
    maxCommon.makedirs(bedDir, quiet=True)
    pslFiles = glob.glob(join(pslDir, "*.psl"))
    logging.debug("Found psl files: %s" % str(pslFiles))
    for pslFname in pslFiles:
        chunkId = splitext(basename(pslFname))[0]
        # we can only parasol check out one single output file
        # but the chainFile command will write the others
        outFile = join(bedDir, chunkId+"."+dbList[0]+".bed")
        cmd = clusterCmdLine("chainFile", pslFname, outFile)
        runner.submit(cmd)
    runner.finish(wait=True)
    logging.info("if batch ok: results written to %s" % bedDir)

def makeRefString(articleData):
    """ prepare a string that describes the citation: 
    vol, issue, page, etc of journal 
    """
    refParts = [articleData.journal]
    if articleData.year!="":
        refParts[0] += (" "+articleData.year)
    if articleData.vol!="":
        refParts.append("Vol "+articleData.vol)
    if articleData.issue!="":
        refParts.append("Issue "+articleData.issue)
    if articleData.page!="":
        refParts.append("Page "+articleData.page)
    return ", ".join(refParts)

def readKeyValFile(fname, inverse=False):
    """ parse key-value tab-sep text file, return as dict integer => string """
    logging.info("Reading %s" % fname)
    fh = open(fname)
    fh.readline()
    dict = {}
    for line in fh:
        fields = line.strip().split("\t")
        if len(fields)>1:
            key, value = fields
        else:
            key = fields[0]
            value = ""
        if inverse:
            key, value = value, key
        dict[int(key)] = value
    return dict

def constructArticleFileId(articleId, fileId):
    " given two integers, articleId and fileId, construct the full fileId (articleId & fileId) "
    articleFileId = (articleId*(10**(pubConf.FILEDIGITS)))+fileId
    return articleFileId

def writeSeqTables(articleDbs, seqDirs, tableDir, fileDescs, annotLinks):
    """  
        write sequences to a <tableDir>/hgFixed.sequences.tab file
        articleDbs is a dict articleId(int) -> list of dbs
        fileDescs is a dict fileId(int) -> description
    """
    # setup output files, write headeres
    logging.info("- Formatting sequence tables to genome browser format")
    dbSeqFiles = {}

    seqTableFname = join(tableDir, "hgFixed.sequenceAnnot.tab")
    seqFh = codecs.open(seqTableFname, "w", encoding="latin1")

    # iterate over seq files, find out dbs for each one and write to output file
    seqFiles = []
    for seqDir in seqDirs:
        dirSeqFiles = glob.glob(join(seqDir, "*.tab"))
        seqFiles.extend(dirSeqFiles)

    logging.info("Filtering %d files from %s to %d files in %s" % \
        (len(seqFiles), str(seqDirs), len(dbSeqFiles), tableDir))
    artWithSeqs = set()
    outRowCount = 0
    inRowCount = 0
    meter = maxCommon.ProgressMeter(len(seqFiles))
    noDescCount = 0

    for fname in seqFiles:
        for annot in maxCommon.iterTsvRows(fname):
            articleId, fileId, seqId = pubGeneric.splitAnnotId(annot.annotId)
            annotId = int(annot.annotId)
            dbs = articleDbs.get(articleId, None)
            if not dbs:
                logging.debug("article %d is not mapped to any genome, not writing any sequence" % articleId)
                continue
            artWithSeqs.add(articleId)
            inRowCount += 1

            # lookup file description
            articleFileId = constructArticleFileId(articleId, fileId)
            #fileDesc, fileUrl = fileDescs.get(str(articleFileId), ("", ""))
            fileDesc, fileUrl = fileDescs[articleFileId]

            # prep data for output table
            annotLinkList   = annotLinks.get(annotId, None)
            if annotLinkList==None:
                annotLinkString=""
            else:
                annotLinkString = ",".join(annotLinkList)

            snippet = pubStore.prepSqlString(annot.snippet, maxLen=1000)
            outRowCount+=1
            if fileDesc == "" or fileDesc==None:
                logging.debug("Cannot find file description for file id %d" % articleFileId)
                noDescCount += 1
            newRow = [ unicode(articleId), unicode(fileId), unicode(seqId), annot.annotId, pubStore.prepSqlString(fileDesc), pubStore.prepSqlString(fileUrl), annot.seq, snippet, annotLinkString]

            # write new sequence row
            seqFh.write(string.join(newRow, "\t"))
            seqFh.write('\n')
        meter.taskCompleted()

    logging.info("Could not find file description for %d sequences" % noDescCount)
    logging.info("%d articles have mapped sequences" % len(artWithSeqs))
    logging.info("Got %d sequences" % inRowCount)
    logging.info("Wrote %d sequences" % outRowCount)
    return artWithSeqs
            
def annotToArticleId(annotId):
    """ map from annotation ID to article Id """
    # number to convert from annotation to article IDs id that are NOT article IDs
    # need to divide by this number to get article Id from annotation ID
    articleDivider = 10**(pubConf.FILEDIGITS+pubConf.ANNOTDIGITS)
    return int(annotId) / articleDivider

def addMarkerDbs(articleDbs, markerArticleFname):
    " add a human genome entry to articleId -> db dict, for all article Ids in file "
    #humanDb = (pubConf.humanDb)
    db = basename(markerArticleFname).split(".")[0]
    articleCount = 0
    logging.debug("Reading %s to find articleIds with markers, db=%s" % (markerArticleFname, db))
    for articleId in open(markerArticleFname):
        articleId = articleId.strip()
        articleDbs[int(articleId)].add(db)
        articleCount += 1
    logging.info("Found %d articles with markers in %s" % (articleCount, markerArticleFname))
    return articleDbs

def sanitizeYear(yearStr):
    """ make sure that the year is really a number:
    split on space, take last element, remove all non-digits, return "0" if no digit found """
    nonNumber = re.compile("\D")
    lastWord = yearStr.split(" ")[-1]
    yearStrClean = nonNumber.sub("", lastWord)
    if yearStrClean=="":
        return "0"
    try:
        year = int(yearStrClean)
    except:
        logging.warn("%s does not look like a year, cleaned string is %s" % (yearStr, yearStrClean))
        year = 0
    return str(year)

def firstAuthor(string):
    " get first author family name and remove all special chars from it"
    string = string.split(" ")[0].split(",")[0].split(";")[0]
    string = "\n".join(string.splitlines()) # get rid of crazy unicode linebreaks
    string = string.replace("\m", "") # old mac text files
    string = string.replace("\n", "")
    string = unidecode.unidecode(string)
    return string

def writeArticleTables(articleDbs, textDir, tableDir, updateIds):
    """ 
        create the articles table based on articleDbs, display Ids and 
        the zipped text file directory.

        also create processedArticles.tab for JSON elsevier script to distinguish between
        processed and no-sequence articles.

    """

    logging.info("- Formatting article information to genome browser format")
    logging.debug("- dbs %s, textDir %s, tableDir %s, updateIds %s" % (articleDbs, textDir, tableDir, updateIds))
    # prepare output files
    artFiles = {}

    articleFname    = join(tableDir, "hgFixed.article.tab")
    articleFh       = codecs.open(articleFname, "w", encoding="utf8")
    extIdFh         = open(join(tableDir, "publications.processedArticles.tab"), "w")

    logging.info("Writing article titles, abstracts, authors")
    articleDict = {}
    articleCount = 0
    for articleData in pubStore.iterArticleDataDir(textDir, updateIds=updateIds):
        artId = int(articleData.articleId)
        doi = pubStore.prepSqlString(articleData.doi)

        # write to processedArticles.tab
        extIdFh.write(articleData.articleId+"\t")
        extId = pubStore.prepSqlString(articleData.externalId, maxLen=2000).encode("utf8")
        extIdFh.write(extId+"\t") # bing-urls contain unicode
        extIdFh.write(doi+"\n")

        artDbs = articleDbs.get(artId, None)
        if not artDbs:
            continue
        logging.debug("article %d has dbs %s" % (artId, str(artDbs)))

        dbString = ",".join(artDbs)
        refString = makeRefString(articleData)
        pmid = str(articleData.pmid)
        if pmid=="" or pmid=="NONE":
            pmid = 0
        
        eIssn = articleData.eIssn
        if eIssn=="":
            eIssn = articleData.printIssn

        prepSql = pubStore.prepSqlString
        articleRow =  (str(artId), \
                       prepSql(articleData.externalId, maxLen=2000), \
                       str(pmid), \
                       prepSql(articleData.doi), \
                       str(articleData.source), \
                       str(articleData.publisher), \
                       prepSql(refString, maxLen=2000), \
                       prepSql(articleData.journal), \
                       prepSql(eIssn), \
                       prepSql(articleData.vol), \
                       prepSql(articleData.issue), \
                       prepSql(articleData.page), \
                       sanitizeYear(articleData.year), \
                       prepSql(articleData.title, maxLen=6000), \
                       prepSql(articleData.authors, maxLen=6000), \
                       firstAuthor(articleData.authors), \
                       prepSql(articleData.abstract, maxLen=32000), \
                       prepSql(articleData.fulltextUrl, maxLen=1000), \
                       dbString)
        articleFh.write(u'\t'.join(articleRow))
        articleFh.write(u'\n')
        articleCount+=1
    logging.info("Written info on %d articles to %s" % (articleCount, tableDir))

def parseBeds(bedDirs):
    """ open all bedFiles in dir, parse out the article Ids from field no 14 (13 zero based),
        return dictionary articleId -> set of dbs and a list of all occuring annotation IDs
        return dictionary annotationId -> list of coordStrings, like hg19/chr1:2000-3000
    """
    logging.info("- Creating a dictionary articleId -> db from bed files")
    bedFiles = []
    for bedDir in bedDirs:
        dirBeds = glob.glob(join(bedDir, "*.bed"))
        bedFiles.extend(dirBeds)
        logging.info("Found %d bed files in directory %s" % (len(dirBeds), bedDir))

    logging.info("Parsing %d bed files" % len(bedFiles))
    articleDbs = defaultdict(set)
    dbPointers = {}
    annotToCoord = {}
    pm = maxCommon.ProgressMeter(len(bedFiles))
    for bedFname in bedFiles:
        db = splitext(basename(bedFname))[0].split(".")[0]
        dbPointers.setdefault(db, db) # this should save some memory
        db = dbPointers.get(db)       # by getting a pointer to a string instead of new object
        for line in open(bedFname):
            fields = line.strip("\n").split("\t")
            chrom, start, end = fields[:3]
            coordString = "%s/%s:%s-%s" % (db, chrom, start, end)
            articleIdStr = fields[3]
            articleId = int(articleIdStr)

            #print list(enumerate(fields))
            annotString = fields[13]
            annotStrings = annotString.split(",")
            annotIds = [int(x) for x in annotStrings]
            for annotId in annotIds:
                annotToCoord.setdefault(annotId, [])
                annotToCoord[annotId].append(coordString)
            #articleId = annotToArticleId(annotIds[0])
            articleDbs.setdefault(articleId, set()).add(db)
        pm.taskCompleted()
    logging.info("Found %d articles with sequences mapped to any genome" % len(articleDbs))
    logging.info("Parsed %d annotationIds linked to coordinates" % len(annotToCoord))
    return articleDbs, annotToCoord

def parseAnnotationIds(pslDir):
    " read all bed files and parse out their annotation IDs "
    pslDirs = glob.glob(join(pslDir, "*"))
    pslFiles = []
    for pslDir in pslDirs:
        pslFiles.extend(glob.glob(join(pslDir, "*")))

    tm = maxCommon.ProgressMeter(len(pslFiles))
    qNames = set()
    for pslDir in pslDirs:
        pslFiles = glob.glob(join(pslDir, "*"))
        for pslFname in pslFiles:
            logging.debug("reading qNames from %s" % pslFname)
            fileQNames = []
            for line in open(pslFname):
                qName = line.split("\t")[9]
                fileQNames.append(qName)
            qNames.update(fileQNames)
            tm.taskCompleted()
    return qNames

def stripArticleIds(hitListString):
    """ remove articleIds from hitList string
        input: a string like 01234565789001000000:23-34,01234567890010000001:45-23 
        return: ("0123456789", "0123456789001000000,01234567890010000001", "23-34,45-23")
    """
    artChars = pubConf.ARTICLEDIGITS
    articleId = hitListString[:artChars]
    seqIds = []
    matchRanges = []
    for matchStr in hitListString.split(","):
        parts = matchStr.split(":")
        seqIds.append(parts[0])
        matchRanges.append(parts[1])
    return articleId, seqIds, matchRanges

def findBedPslFiles(bedDir):
    " find all pairs of bed and psl files with same basename in input dirs and return list of basenames "
    # get all input filenames
    logging.info("Looking for bed and psl files in dir %s" % str(bedDir))
    bedFiles = glob.glob(join(bedDir, "*.bed"))
    pslFiles = glob.glob(join(bedDir, "*.psl"))
    # there can be a bed file without a psl file if a job had no results
    # and the job created the empty bed just to signal job completion
    # for the cluster system
    #assert(len(bedFiles)==len(pslFiles))
    logging.info("Found %d files in dir %s" % (len(bedFiles), str(bedDir)))
    bedBases = set([splitext(b)[0] for b in bedFiles])
    pslBases = set([splitext(b)[0] for b in pslFiles])
    basenames = set(bedBases).intersection(pslBases)

    logging.info("Total: %d bed/psl files" % (len(basenames)))
    return basenames

def readReformatBed(bedFname, artDescs, artClasses, impacts, dataset, annotLoci):
    """ read bed, return as dict, indexed by articleId. 
    
        Add various extra fields to bed, like journal, title from artDescs,
        a category from artClasses and impact factors.
        Special case for a dataset named yif. 
        (yif = yale image finder) as figures are a class of their own.
    """
    bedLines = {}
    for line in open(bedFname):
        fields = line.strip("\n").split("\t")
        bedName   = fields[3]
        articleId, seqIds, seqRanges = stripArticleIds(bedName)
        fields[3] = articleId
        fields.append(",".join(seqIds))
        fields.append(",".join(seqRanges))
        fields[5] = "" # remove strand 
        articleIdInt = int(articleId)

        art = artDescs[articleIdInt]
        issn = art.printIssn
        impact = impacts.get(issn, 0)

        # translate 
        artDescFields = (art.publisher, art.pmid, art.doi, \
            art.printIssn, art.journal, art.title, art.firstAuthor, art.year)
        artDescFields = [pubStore.prepSqlString(f, maxLen=255) for f in artDescFields]
        fields.extend(artDescFields) # don't add the articleId itself
        fields.append(str(impact))

        # add the class field
        classes = artClasses.get(articleIdInt, [])
        if dataset=="yif":
            classes=["yif"]
        fields.append(",".join(classes))

        # add the locus field
        loci = set()
        for seqId in seqIds:
            loci.update(annotLoci[seqId])
        locusStr = ",".join(loci)
        fields.append(locusStr)


        bedLine = "\t".join(fields)
        bedLines.setdefault(articleIdInt, []).append(bedLine)
    return bedLines

def openBedPslOutFiles(basenames, dbList, tableDir):
    """ open two file handles for each basename like /path/path2/0000.hg19.bed
    and return as two dict db -> filehandle """
    outBed = {}
    outPsl = {}
    for db in dbList:
        outFname    = join(tableDir, db+".blat.bed")
        outBed[db] = open(outFname, "w")
        outPslFname = join(tableDir, db+".blatPsl.psl")
        outPsl[db] = open(outPslFname, "w")
    return outBed, outPsl

def closeAllFiles(list):
    for l in list:
        l.close()

def appendPslsWithArticleId(pslFname, articleIds, outFile):
    " append all psls in pslFname that have a qName in articleIds to outFile, append a field"
    for line in open(pslFname):
        psl = line.strip().split("\t")
        qName = psl[9]
        articleId = annotToArticleId(qName)
        if articleId in articleIds:
            articleIdStr = psl[9][:pubConf.ARTICLEDIGITS]
            psl.append(articleIdStr)
            outFile.write("\t".join(psl))
            outFile.write("\n")

def sortBedFiles(tableDir):
    " sort all bed files in directory "
    logging.info("Sorting all bed files in %s with unix sort" % tableDir)
    for bedFname in glob.glob(join(tableDir, "*.bed")):
        logging.info("%s..." % bedFname)
        cmd = "sort -k1,1 -k2,2n %s -o %s" % (bedFname, bedFname)
        maxCommon.runCommand(cmd, verbose=False)

def rewriteFilterBedFiles(bedDir, tableDir, dbList, artDescs, artClasses, impacts, annotLoci, dataset):
    """ 
    add extended columns with annotationIds, impact, issn, etc
    """
    logging.info("- Formatting bed files for genome browser")
    basenames = findBedPslFiles(bedDir)
    outBed, outPsl = openBedPslOutFiles(basenames, dbList, tableDir)
        
    featCounts    = {}
    dropCounts    = {}
    dropArtCounts = {}
    logging.info("Concatenating and reformating bed and psl files to bed+/psl+")
    pm = maxCommon.ProgressMeter(len(basenames))
    for inBase in basenames:
        bedFname = inBase+".bed"
        pslFname = inBase+".psl"
        bedBase = basename(bedFname)
        db      = bedBase.split(".")[1]
        outName = join(tableDir, bedBase)
        featCounts.setdefault(db, 0)
        dropCounts.setdefault(db, 0)
        dropArtCounts.setdefault(db, 0)

        logging.debug("Reformatting %s to %s" % (bedFname, outName))

        bedLines = readReformatBed(bedFname, artDescs, artClasses, impacts, dataset, annotLoci)
        articleIds = set()
        outFh   = outBed[db]
        for articleId, bedLines in bedLines.iteritems():
            for lineNew in bedLines:
                outFh.write(lineNew)
                outFh.write("\n")
                featCounts[db] += 1
            articleIds.add(articleId)

        appendPslsWithArticleId(pslFname, articleIds, outPsl[db])
        pm.taskCompleted()

    logging.info("features that were retained")
    for db, count in featCounts.iteritems():
        logging.info("Db %s: %d features kept, %d feats (%d articles) dropped" % \
            (db, count, dropCounts[db], dropArtCounts[db]))
    logging.info("bed output written to directory %s" % (tableDir))
    closeAllFiles(outBed.values())
    closeAllFiles(outPsl.values())
    
def mustLoadTable(db, tableName, tabFname, sqlName, append=False):
    if append:
        appendOpt = "-oldTable "
    else:
        appendOpt = ""

    if isfile(tabFname):
        cmd = "hgLoadSqlTab %s %s %s %s %s" % (db, tableName, sqlName, tabFname, appendOpt)
        maxCommon.runCommand(cmd, verbose=False)
    else:
        logging.warn("file %s not found" % tabFname)

def mustLoadBed(db, tableName, bedFname, sqlTable=None, append=False):
    if isfile(bedFname):
        opts = "-tab"
        if append:
            opts = opts+ " -oldTable "

        if sqlTable:
            opts = opts + " -sqlTable=%s -renameSqlTable" % sqlTable
        cmd = "hgLoadBed %s %s %s %s -tab" % (db, tableName, bedFname, opts)
        maxCommon.runCommand(cmd, verbose=False)
    else:
        logging.error("file %s not found" % bedFname)
        sys.exit(0)

def loadTable(db, tableName, fname, sqlName, fileType, tableSuffix, appendMode):
    """ load tab sql table or bed file, append tableSuffix if not append """
    if not isfile(fname):
        logging.warn("File %s not found, skipping" % fname)
        return 
    if getsize(fname)==0:
        logging.warn("File %s has zero file size, skipping" % fname)
        return

    # seq match bed file
    if not appendMode:
        tableName = tableName+tableSuffix

    if fileType=="bed":
        mustLoadBed(db, tableName, fname, sqlName, appendMode)
    else:
        mustLoadTable(db, tableName, fname, sqlName, appendMode)

    return tableName

def upcaseFirstLetter(string):
    return string[0].upper() + string[1:] 

def filterBedAddCounts(oldBed, bedFh, counts, markerType):
    " write bed from oldBed to newBed, keeping only features name in counts, add one field with count "
    logging.info("Filtering bed %s to %s" % (oldBed, bedFh.name))
    #ofh = open(newBed, "w")
    readCount = 0
    writeCount = 0
    for line in open(oldBed):
        fields = line.strip().split("\t")
        name = fields[3]
        count = counts.get(name, 0)
        readCount += 1
        if count==0:
            continue
        fields.append("%d" % count)
        fields.append(markerType)
        bedFh.write("\t".join(fields))
        bedFh.write("\n")
        writeCount += 1
    logging.info("Kept %d features out of %d features" % (writeCount, readCount))
    
def getMarkers(markerDbDir):
    " return a list of (markerType, db, bedFname) with a list of all markers we have on disk "
    res = []
    markerBeds = glob.glob(join(markerDbDir, "*.bed"))
    for markerFname in markerBeds:
        db, markerType, ext = basename(markerFname).split(".")
        res.append( (db, markerType, markerFname) )
        assert("hg19" != markerType)
    return res

def findRewriteMarkerBeds(dirs, markerDbDir, markerOutDir, skipSnps, tableSuffix=""):
    """
    dirs is a list of PipelineConfig objects.

    search all batches for markerCounts.tab to get a list of counts for each
    marker. Use this dictionary to filter the bed files in markerDbDir, add the counts as an
    extended bed field, write beds to <markerOutDir>/<db>.marker<type>.bed 
    
    return fileDict as ["marker"<type>][db] -> file name
    """
    logging.info("Writing marker bed files to %s, adding counts of matching articles" % markerOutDir)
    if not isdir(markerOutDir):
        os.mkdir(markerOutDir)
    else:
        if len(os.listdir(markerOutDir))!=0:
            logging.info("Deleting all files in %s" % markerOutDir)
            shutil.rmtree(markerOutDir)
            os.mkdir(markerOutDir)

    counts = collections.defaultdict(int)
    for datasetDir in dirs:
        counts = datasetDir.readMarkerCounts(counts)
    if len(counts)==0:
        raise Exception("No counts found for any markers and all datasets")

    markerTypes = getMarkers(markerDbDir)

    fnameDict = {}
    fileDict = {}
    #dbs = set([mt[0] for my in markerTypes])
    for db, markerType, inputBedFname in markerTypes:
    #for db in dbs:
        if skipSnps and markerType=="snp":
            logging.info("Skipping SNPs to gain speed")
            continue
        upMarkerType = upcaseFirstLetter(markerType) # e.g. snp -> Snp
        #newBedFname = join(markerOutDir, db+".marker%s.bed" % upMarkerType)
        newBedFname = join(markerOutDir, db+".marker.bed")
        if newBedFname in fileDict:
            bedFh = fileDict[newBedFname]
        else:
            bedFh = open(newBedFname, "w")
            fileDict[newBedFname] = bedFh
        filterBedAddCounts(inputBedFname, bedFh, counts, markerType)

        # keep track of the filenames for later loading
        #tableName = "marker"+upMarkerType # e.g. markerBand
        #tableName = "marker"+upMarkerType # e.g. markerBand
        tableName = "marker"
        fnameDict[(tableName, "bed")] = {}
        fnameDict[(tableName, "bed")][db] = [newBedFname]
    return fnameDict

def findUpdates(baseDir, updateId):
    " search baseDir for possible update directories, if updateId !=0 otherwise return just updateId "
    updates = []
    updateDir = join(baseDir, "updates")
    if updateId!=None:
        logging.debug("Baseline loading, only loading updateId %s" % updateId)
        updates = [updateId]
    elif updateId==None and isdir(updateDir):
        updates = os.listdir(updateDir)
        logging.debug("Baseline loading, also running on these updates: %s" % updates)
    return updates

#def cleanupDb(prefix, db):
    #" drop temporary pubs loading tables "
    #maxMysql.dropTablesExpr(db, prefix+"%New")
    #maxMysql.dropTablesExpr(db, prefix+"%Old")
    
#def safeRenameTables(newTableNames, suffix, tmpSuffix):
    #" Rename all tables "
    #finalTableNames = [t.replace(suffix, "") for t in newTableNames]
    #oldTableNames = [t+"Old" for t in finalTableNames]
    #logging.debug("Safe Renaming: new names: %s" % (newTableNames))
    #logging.debug("Safe Renaming: old names: %s" % (oldTableNames))
    #logging.debug("Safe Renaming: final names: %s" % (finalTableNames))

    #maxMysql.renameTables("hg19", finalTableNames, oldTableNames, checkExists=True)
    #maxMysql.renameTables("hg19", newTableNames, finalTableNames)
    #maxMysql.dropTables("hg19", oldTableNames)

def loadTableFiles(dbTablePrefix, fileDict, dbList, sqlDir, appendMode, \
        suffix="", dropFirst=False, loadArticles=True):
    """ load all article and seq tables for a list of batchIds 
    return list of loaded tables in format: <db>.<tableName> 

    fileDict is:
    (tableName, [bed|tab]) -> db -> filenames
    e.g. 
    blat, bed -> hg19 -> [fname]
    """
    logging.debug("Loading tables from %s for %s, append Mode %s" % (fileDict, dbList, appendMode))

    #for db in dbList:
        #cleanupDb(dbTablePrefix, db)
    #cleanupDb(dbTablePrefix, "hgFixed")

    # dropFirst: remove all tables before loading them
    if dropFirst:
        logging.info("Before appending to marker bed tracks, dropping the old ones first")
        dropTables = {}
        for (tableBaseName, fileType), dbFnames in fileDict.iteritems():
            upTableBase = upcaseFirstLetter(tableBaseName)
            for db, fnames in dbFnames.iteritems():
                dbTableName = dbTablePrefix + upTableBase
                dropTables.setdefault(db, set()).add(dbTableName)
        for db, tableNames in dropTables.iteritems():
            maxMysql.dropTables(db, tableNames )

    sqlFilePrefix = "pubs"
    dbTables = set()
    for (tableBaseName, fileType), dbFnames in fileDict.iteritems():
        upTableBase = upcaseFirstLetter(tableBaseName)
        for db, fnames in dbFnames.iteritems():
            if db.startswith("nonUcsc_"):
                continue
            for fname in fnames:
                # some datasets refer to article information from others
                #if tableBaseName.endswith("article") and "yif" in :
                    #logging.info("Skipping article information")
                    #continue
                # find the right .sql file
                if tableBaseName.startswith("marker") and not tableBaseName.startswith("markerAnnot"):
                    sqlName = join(sqlDir, sqlFilePrefix+"Marker.sql")
                else:
                    sqlName = join(sqlDir, sqlFilePrefix+upTableBase+".sql")

                dbTableName = dbTablePrefix + upTableBase
                loadedName = loadTable(db, dbTableName, fname, sqlName, fileType, suffix, appendMode)
                if loadedName!=None:
                    dbTables.add(db+"."+loadedName)

    logging.debug("Loaded these tables: %s" % dbTables)
    return dbTables
        
def queryLoadedFnames(db, table):
    """ connect to mysql db and read loaded filenames from table pubsLoadedFile, 
        return as dict fname => (fsize (int) , time) """
    logging.debug("Loading already loaded filenames from table %s" % table)
    rows = maxMysql.hgGetAllRows(db, table, pubConf.TEMPDIR)
    data = {}
    for row in rows:
        fname, fsize, time = row
        if fname in data:
            raise Exception("fname %s appears twice in table %s" % (fname, table))
        data[fname] = (int(fsize), time)
    return data

def createLoadedFileTable(sqlDir, procDb, tableName):
    " create pubsLoadedFile table "
    logging.debug("Creating new table %s" % tableName)
    sqlFname = join(sqlDir, "pubsLoadedFile.sql")
    #cmd = 'hgsql %s < %s' % (procDb, sqlFname)
    #maxCommon.runCommand(cmd)
    maxMysql.execSqlCreateTableFromFile(procDb, sqlFname, tableName)

def getLoadedFiles(procDb, procTable):
    " read already loaded files from mysql tracking trable or create an empty one "
    sqlDir = pubConf.sqlDir

    if maxMysql.tableExists(procDb, procTable):
        alreadyLoadedFnames = queryLoadedFnames(procDb, procTable)
        logging.debug("These files have already been loaded: %s" % alreadyLoadedFnames)
    else:
        createLoadedFileTable(sqlDir, procDb, procTable)
        alreadyLoadedFnames = []
    return alreadyLoadedFnames

def appendFilenamesToSqlTable(fileDicts, trackDb, trackingTable, ignoreDir):
    " given dict (table, ext) -> list of filenames, write filenames/size to mysql table "
    # example fileDicts: 
    # [{(u'blatPsl', u'psl'): {u'xenTro2': [u'/hive/data/inside/literature
    # /blat/pmc/batches/1_0/tables/xenTro2.blatPsl.psl']}}]
    logging.debug("FileDicts is %s, appending these to tracking table %s" % (fileDicts, trackingTable))
    for fileDict in fileDicts:
        for dbFnames in fileDict.values():
            for db, fileNameList in dbFnames.iteritems():
                for fname in fileNameList:
                    if ignoreDir in fname:
                        logging.debug("not appending %s, is in temporary dir" % fname)
                        continue
                    fileSize = os.path.getsize(fname)
                    maxMysql.insertInto(trackDb, trackingTable, ["fileName","size"], [fname, fileSize])


def isIdenticalOnDisk(loadedFiles):
    """
    check if files on disk have same size as files in the DB. return true if they are.
    """
    if len(loadedFiles)==0:
        return True

    for fname, sizeDate in loadedFiles.iteritems():
        size, date = sizeDate
        size = int(size)
        if not isfile(fname):
            logging.error("File %s does not exist on disk but is loaded in DB" % fname)
            return False
        diskSize = getsize(fname)
        if diskSize!=size:
            logging.error("File %s has size %d on disk but the version in the DB has size %d" % \
                (fname, diskSize, size))
            return False
    return True

def checkIsIdenticalOnDisk(loadedFiles, trackingDb, trackingTable):
    " throw exception if there is any difference between loaded files in DB and on disk "
    if not isIdenticalOnDisk(loadedFiles):
        raise Exception("Old files already loaded into DB (%s.%s) are different "
            "from the ones on disk. You can run hgsql -e 'truncate %s.%s' to reload everything." %\
                (trackingDb, trackingTable, trackingDb, trackingTable))


def initTempDir(dirName):
    " create temp dir with dirName and delete all contents "
    tempMarkerDir = join(pubConf.TEMPDIR, "pubMapLoadTempFiles")
    if isdir(tempMarkerDir):
        shutil.rmtree(tempMarkerDir)
    os.makedirs(tempMarkerDir)
    return tempMarkerDir

def runLoadStep(datasetList, dbList, markerCountBasename, markerOutDir, userTablePrefix, skipSnps, baseOutDir):
    """ 
        Loads files that are NOT yet in hgFixed.pubLoadedFile
        Also generates the bed files with article counts for the markers
    """

    datasets = datasetList.split(",")

    tablePrefix = "pubs" + userTablePrefix

    sqlDir          = pubConf.sqlDir
    markerDbDir     = pubConf.markerDbDir
    trackingTable   = tablePrefix+"LoadedFiles"
    trackingDb      = "hgFixed"

    loadedFilenames = getLoadedFiles(trackingDb, trackingTable)
    checkIsIdenticalOnDisk(loadedFilenames, trackingDb, trackingTable)
    append          = (len(loadedFilenames) != 0) # append if there is already old data in the db

    # first create the marker bed files (for all basedirs) and load them
    # this is separate because we pre-calculate the counts for all marker beds
    # instead of doing this in hgTracks on the fly
    datasetDirs = [pubMapProp.PipelineConfig(dataset, baseOutDir) for dataset in datasets]
    tempMarkerDir = initTempDir("pubMapLoadTemp")

    markerFileDict = findRewriteMarkerBeds(datasetDirs, markerDbDir, tempMarkerDir, skipSnps)
    markerTables = loadTableFiles(tablePrefix, markerFileDict, dbList, sqlDir, append, dropFirst=True)

    fileDicts = [markerFileDict]
    tableNames = set(markerTables)

    logging.info("Now loading non-marker files")
    # now load non-marker data from each basedir
    # but only those that are not yet in the DB
    for datasetDir in datasetDirs:
        logging.info("Loading non-marker files for dataset %s" % datasetDir.dataset)
        # yif dataset links to PMC articles, must not load articles
        # otherwise duplicated primary IDs in article table
        loadArticles = (datasetDir.dataset != "yif")
        # find name of table files
        batchIds = datasetDir.findBatchesAtStep("tables")
        if len(batchIds)==0:
            logging.info("No completed batches for dataset %s, skipping it" % datasetDir.dataset)
            continue
        fileDict = datasetDir.findTableFiles(loadedFilenames)
        fileDicts.append(fileDict)

        # load tables into mysql
        dirTableNames = loadTableFiles(tablePrefix, fileDict, dbList, sqlDir, append, \
            loadArticles=loadArticles)
        tableNames.update(dirTableNames)
        append = True # all subsequent baseDirs must append now to the tables

    # update tracking table with filenames
    appendFilenamesToSqlTable(fileDicts, trackingDb, trackingTable, tempMarkerDir)

def submitFilterJobs(runner, chunkNames, inDir, outDir, isProt=False):
    """ submit jobs to clear annotation file from duplicate sequences"""
    logging.info("Filtering sequences: Removing duplicates and short sequences")
    maxCommon.mustBeEmptyDir(outDir, makeDir=True)

    filterCmd = "filterSeqFile"
    if isProt:
        filterCmd = "filterProtSeqFile"

    logging.info("Reading from %s, writing to %s (%d chunks to annotate)" % (inDir, outDir, len(chunkNames)))
    for chunkName in chunkNames:
        inFname = join(inDir, chunkName+".tab.gz")
        outFname = join(outDir, chunkName+".tab")
        cmd = clusterCmdLine(filterCmd, inFname, outFname)
        runner.submit(cmd)
        
def filterSeqFile(inFname, outFname, isProt=False):
    " skip annotation lines if sequence has been seen for same article "
    alreadySeenSeq = {} # to ignore duplicated sequences
    outFh = codecs.open(outFname, "w", encoding="utf8")

    headerLine = gzip.open(inFname).readline()
    outFh.write(headerLine)

    minLen = pubConf.minSeqLen
    maxLen = pubConf.maxSeqLen

    if isProt:
        minLen = pubConf.minProtSeqLen

    logging.debug("Filtering file %s" % inFname)
    for row in maxCommon.iterTsvRows(inFname, encoding="utf8"):
        articleId, dummy1, dummy2 = pubGeneric.splitAnnotId(row.annotId)
        alreadySeenSeq.setdefault(articleId, set())
        if row.seq in alreadySeenSeq[articleId]:
            continue
        if len(row.seq) < minLen:
            continue
        if len(row.seq) > maxLen:
            continue
        alreadySeenSeq[articleId].add(row.seq)
        outFh.write(u"\t".join(row))
        outFh.write("\n")
    outFh.close()

def writeUnmappedSeqs(annotIds, inDir, outDir):
    """ read all tab files in seqDir, skip all seqs with annotIds, 
    write all others to unmapDir """
    logging.info("Writing sequences that do not match genome to cdna files")
    maxCommon.mustBeEmptyDir(outDir, makeDir=True)
    inFiles = glob.glob(join(inDir, "*.tab"))
    logging.info("Found %d .tab files in %s" % (len(inFiles), inDir))
    pm = maxCommon.ProgressMeter(len(inFiles))

    for inFname in inFiles:
        logging.debug("Filtering sequence file %s" % inFname)
        inBase = basename(inFname)
        outFname = join(outDir, inBase)
        outFh = codecs.open(outFname, "w", encoding="utf8")
        headerLine = open(inFname).readline()
        outFh.write(headerLine)
        for row in maxCommon.iterTsvRows(inFname):
            annotId = int(row.annotId)
            if annotId in annotIds:
                continue
            else:
                outFh.write("\t".join(row))
                outFh.write("\n")
        outFh.close
        pm.taskCompleted()
    
def liftCdna(inDir, outDir):
    " lift cdna psl files to genome coord psls"
    dbList  = pubConf.alignGenomeOrder
    cdnaDir = pubConf.cdnaDir
    maxCommon.mustBeEmptyDir(outDir, makeDir=True)
    for db in dbList:
        logging.info("Lifting CDna of db %s" % db)
        pslFile = join(inDir, db+".psl")
        outFile = join(outDir, db+".psl")

        mapMask = join(cdnaDir, db, "*.psl")
        mapPsls = glob.glob(mapMask)
        if len(mapPsls)==0:
            logging.warn("File %s not found, skipping organism" % mapMask)
            continue
        mapPsl = mapPsls[0]
        
        if not isfile(pslFile) or not isfile(mapPsl):
            logging.warn("File %s not found, skipping organism")
            continue

        assert(len(mapPsls)<=1)
        
        cmd = "pslMap %s %s %s" % (pslFile, mapPsl, outFile)
        maxCommon.runCommand(cmd)
    
def rewriteMarkerAnnots(markerAnnotDir, db, tableDir, fileDescs, markerArticleFile, markerCountFile):
    " rewrite marker annot tables for mysql and write articleIds to file "
    # open outfiles
    idFh = open(markerArticleFile, "w")
    markerCountFh = open(markerCountFile, "w")
    outFname = join(tableDir, db+".markerAnnot.tab")
    tmpFname = tempfile.mktemp(dir=pubConf.TEMPDIR, prefix=db+".markerAnnot", suffix=".unsorted.tab")
    logging.info("Rewriting marker tables from %s to %s, articleIds to %s" \
        % (markerAnnotDir, tmpFname, markerArticleFile))
    outFile = codecs.open(tmpFname, "w", encoding="utf8")

    # init vars
    fnames = glob.glob(join(markerAnnotDir, "*.tab.gz"))
    meter = maxCommon.ProgressMeter(len(fnames))
    outRowCount = 0
    markerCounts = defaultdict(int) # store the count of articles for each marker

    for fname in fnames:
        # the list of article Ids for each marker in current file
        fileMarkerArticles = defaultdict(set)
        for row in maxCommon.iterTsvRows(fname):
            articleId, fileId, annotId = pubGeneric.splitAnnotIdString(row.annotId)
            fullFileId = articleId+fileId
            snippet = pubStore.prepSqlString(row.snippet, maxLen=3000)
            fileAnnot = fileDescs[int(fullFileId)]
            fileDesc, fileUrl = fileAnnot
            #if row.type not in ["band", "snp", "symbol"]:
                #continue
            newRow = [articleId, fileId, annotId, fileDesc, fileUrl, \
                row.type, row.markerId, row.recogType, row.recogId, row.section, unicode(snippet)]
            fileMarkerArticles[row.markerId].add(articleId)

            outFile.write(u'\t'.join(newRow))
            outFile.write('\n')
            outRowCount+=1

        articleIds = set()
        for markerId, articleIdSet in fileMarkerArticles.iteritems():
            markerCounts[markerId]+= len(articleIdSet)
            articleIds.update(articleIdSet)

        for articleId in articleIds:
            idFh.write(articleId+"\n")
        meter.taskCompleted()
    logging.info("Wrote %d rows to %s for %d markers" % (outRowCount, tmpFname, len(markerCounts)))

    # sort table by markerId = field 7 
    util.sortTable(tmpFname, outFname, 7)
    os.remove(tmpFname)

    logging.info("Writing marker counts")
    for markerId, count in markerCounts.iteritems():
        markerCountFh.write("%s\t%d\n" % (markerId, count))
        
def switchOver():
    """ For all databases: drop all pubsBakX, rename pubsX to pubsBakX, rename pubsDevX to pubsX
    """
    dbs = pubConf.alignGenomeOrder
    dbs.insert(0, "hgFixed")
    prodTables = []
    devTables = []
    bakTables = []
    for db in dbs:
        maxMysql.dropTablesExpr(db, "pubsBak%")
        maxMysql.dropTablesExpr(db, "pubsTest%")
        allTables = maxMysql.listTables(db, "pubs%")
        dbDevTables = [t for t in allTables if t.startswith("pubsDev")]
        notDbDevTables = set(allTables).difference(dbDevTables)
        devTables.extend([db+"."+x for x in dbDevTables])
        prodTables.extend([db+"."+x.replace("Dev","") for x in dbDevTables])
        bakTables.extend([db+"."+x.replace("pubs", "pubsBak") for x in notDbDevTables])
        
    logging.info("Safe Renaming: dev names: %s" % (devTables))
    logging.info("Safe Renaming: prod names: %s" % (prodTables))
    logging.info("Safe Renaming: bak names: %s" % (bakTables))

    maxMysql.renameTables("hg19", prodTables, bakTables, checkExists=True)
    maxMysql.renameTables("hg19", devTables, prodTables)

def annotToCdr3(dataset):
    """ export putative CDR3s from a list of datasets to .tab and .fa
        to pubConf.cdr3Dir
    """
    pubList = dataset.split(",")
    annotDir = pubConf.annotDir

    maxCommon.mustExistDir(pubConf.cdr3Dir, makeDir=True)

    outFaFname = join(pubConf.cdr3Dir, "cdr3.fa")
    outTabFname = join(pubConf.cdr3Dir, "cdr3.tab")
    faFh = open(outFaFname, "w")
    tabFh = open(outTabFname, "w")
    logging.info("Filtering prot sequences to fasta %s and .tab" % (outFaFname))
    wroteHeaders = False

    for dataset in pubList:
        logging.info("Processing dataset %s" % dataset)

        annotMask = join(pubConf.pubMapBaseDir, dataset, "batches", "*", "annots", "prot", "*")
        annotFnames = glob.glob(annotMask)
        logging.debug("Found dirs: %s" % annotFnames)
        pm = maxCommon.ProgressMeter(len(annotFnames))
        for annotFname in annotFnames:
            if not wroteHeaders:
                headerLine = maxbio.openFile(annotFnames[0]).readline()
                logging.debug("Writing header line from %s as %s" % (annotFnames[0], headerLine))
                tabFh.write(headerLine)
                wroteHeaders = True

            for row in pubCdr3Filter.iterCdr3Rows(annotFname):
                if row.prefixFilterAccept!="Y" or row.suffixFilterAccept!="Y" or \
                    row.markovFilterAccept!="Y":
                    continue
                tabFh.write(u'\t'.join(row).encode("utf8"))
                tabFh.write('\n')
                faFh.write(">"+row.annotId+"\n")
                faFh.write(row.seq+"\n")
            pm.taskCompleted()

    faFh.close()
    tabFh.close()
    logging.info("Output written to %s and %s" % (outTabFname, outFaFname))

def annotToFasta(dataset, useExtArtId=False):
    """ export one or more datasets to fasta files 
    output is written to pubConf.faDir
    
    """
    pubList = dataset.split(",")
    annotDir = pubConf.annotDir
    artMainIds = {}
    
    if useExtArtId:
        for pub in pubList:
            logging.info("Processing %s" % pub)
            textDir = join(pubConf.textBaseDir, pub)
            logging.info("Reading article identifiers, titles, citation info")
            for article in pubStore.iterArticleDataDir(textDir):
                if article.pmid!="":
                    mainId = article.pmid
                elif article.doi!="":
                    mainId = article.doi
                elif article.externalId!="":
                    mainId = article.externalId

                mainId += " "+article.title+" "+article.journal+" "+article.year
                artMainIds[int(article.articleId)] = mainId
        
    #annotDir = pubConf.annotDir
    annotTypes = ["dna", "prot"]
    maxCommon.mustExistDir(pubConf.faDir, makeDir=True)
    for pub in pubList:
        logging.info("Processing dataset %s" % pub)
        for annotType in annotTypes:
            outFname = join(pubConf.faDir, pub+"."+annotType+".fa")
            outFh = codecs.open(outFname, "w", encoding="utf8")
            logging.info("Reformatting %s sequences to fasta %s" % (annotType, outFname))
            #tabDir = join(annotDir, annotType, pub)
            annotMask = join(pubConf.pubMapBaseDir, pub, "batches", "*", "annots", annotType, "*")
            annotFnames = glob.glob(annotMask)
            logging.debug("Found dirs: %s" % annotFnames)
            pm = maxCommon.ProgressMeter(len(annotFnames))
            for annotFname in annotFnames:
                for row in maxCommon.iterTsvRows(annotFname):
                    articleId = int(row.annotId[:pubConf.ARTICLEDIGITS])
                    if useExtArtId:
                        seqId = artMainIds.get(articleId, "")
                        outFh.write(">"+row.annotId+"|"+seqId+"\n")
                    else:
                        outFh.write(">"+row.annotId+"\n")

                    outFh.write(row.seq+"\n")
                pm.taskCompleted()
        logging.info("Output written to %s" % outFname)

def runStepSsh(host, dataset, step):
    " run one step of pubMap on a different machine "
    opts = " ".join(sys.argv[3:])
    python = sys.executable
    mainProg = sys.argv[0]
    mainProgPath = join(os.getcwd(), mainProg)

    cmd = "ssh %(host)s %(python)s %(mainProgPath)s %(dataset)s %(step)s %(opts)s" % locals()
    logging.info("Executing command %s" % cmd)
    ret = os.system(cmd)
    if ret!=0:
        logging.info("error during SSH")
        sys.exit(1)

def runAnnotStep(d, onlyMarkers=False, onlySeq=False):
    """ 
    run jobs to annotate the text files, directory names for this are 
    stored as attributes on the object d
    """
    # if the old batch is not over tables yet, squirk and die
    if d.batchId!=None and d.batchIsPastStep("annot") and not d.batchIsPastStep("tables"):
        raise Exception("Found one batch in %s that is past annot but not past tables yet. "
            "A previous run might have crashed. You can try rm -rf %s to restart this batch" % \
                (d.progressDir, d.batchDir))

    d.createNewBatch()
    # the new batch must not be over annot yet
    if d.batchIsPastStep("annot"):
        raise Exception("Annot was already run on this batch, see %s. Stopping." % \
            d.progressDir)

    # find text updates to annotate
    d.findUnannotatedUpdateIds()
    if d.updateIds==None or len(d.updateIds)==0:
        maxCommon.errAbort("All data files have been processed. Skipping all steps.")

    # get common uppercase words for protein filter
    maxCommon.mustExistDir(d.markerAnnotDir, makeDir=True)
    if not onlyMarkers:
        maxCommon.mustExistDir(d.dnaAnnotDir, makeDir=True)
        maxCommon.mustExistDir(d.protAnnotDir, makeDir=True)
        wordCountBase = "wordCounts.tab"
        runner = d.getRunner("upcaseCount")
        wordFile = countUpcaseWords(runner, d.baseDir, wordCountBase, d.textDir, d.updateIds)
    else:
        wordFile = ""

    # submit jobs to batch system to run the annotators on the text files
    # use the startAnnoId parameter to avoid duplicate annotation IDs
    # aid = annotationId
    aidOffset = pubConf.specDatasetAnnotIdOffset.get(d.dataset, 0) # special case for e.g. yif

    if onlyMarkers:
        outDirs = "%s" % (d.markerAnnotDir)
        algNames = "markerSearch.py"
    if onlySeq:
        outDirs = "%s,%s" % (d.dnaAnnotDir, d.protAnnotDir)
        algNames = "dnaSearch.py:Annotate,protSearch.py"
    else:
        outDirs = "%s,%s,%s" % (d.markerAnnotDir, d.dnaAnnotDir, d.protAnnotDir)
        # run the MarkerAnnotate class in markerSearch.py, 
        # the Annotate class in dnaSearch.py and the annotate-function in protSearch.py
        algNames = "markerSearch.py:MarkerAnnotate,dnaSearch.py:Annotate,protSearch.py"

    options = {"wordFile":wordFile, \
        "startAnnotId.dnaSearch":0+aidOffset, "startAnnotId.protSearch":15000+aidOffset, \
        "startAnnotId.markerSearch" : 30000+aidOffset }
    runner = pubGeneric.makeClusterRunner("pubMap-annot-"+d.dataset)
    chunkNames = pubAlg.annotate(
        algNames, d.textDir, options, outDirs, updateIds=d.updateIds, \
        cleanUp=True, runNow=True, runner=runner)

    d.writeChunkNames(chunkNames)
    d.writeUpdateIds()
    d.appendBatchProgress("annot")

    # re-read the list of chunks just annotated
    d._defineBatchDirectories()

def parseFileDescs(fname):
    res = {}
    for row in maxCommon.iterTsvRows(fname):
        res[int(row.fileId)] = (row.desc, row.url)
    return res
        
def parseArtDescs(fname):
    " read article descriptions into memory (can be very big, several gbs) "
    logging.info("Parsing %s" % fname)
    res = {}
    for row in maxCommon.iterTsvRows(fname):
        #res[int(r.articleId)] = (r.publisher, r.pmid, r.doi, r.printIssn, r.journal, r.title, r.firstAuthor, r.year)
        res[int(row.articleId)] = row
    return res

def parseImpacts(fname):
    """ parse file with columns ISSN and impact, return as dict string -> float 
    """
    logging.info("Parsing impact factors from %s" % fname)
    res = {}
    #maxImp = 25.0
    for row in maxCommon.iterTsvRows(fname):
        if row.impact.strip()=="":
            continue
        impact = float(row.impact)
        #impVal = int(min(impact,maxImp) * (255/maxImp))
        res[row.ISSN] = int(round(impact))
    return res
        
def parseArtClasses(textDir, updateIds):
    " read article classes into memory."
    # XX use the updateIds!!
    #fname = join(textDir, "docClasses.tab.gz")
    fname = pubConf.classFname
    #if not isfile(fname):
        #return {}
    logging.info("Parsing article classes from %s" % fname)
    res = {}
    for r in maxCommon.iterTsvRows(fname):
        res[int(r.articleId)] = (r.classes.split(","))
    logging.info("Found article classes for %d articles" % len(res))
    return res

def runTablesStep(d, options):
    " generate table files for mysql "
    # this step creates tables in batchDir/tables
    if not options.skipConvert:
        maxCommon.mustBeEmptyDir(d.tableDir, makeDir=True)

    logging.info("Reading file descriptions")
    # reformat bed and sequence files
    if not options.skipConvert:
        # load all extended bed+ fields data into memory
        artDescs   = parseArtDescs(d.artDescFname)
        artClasses = parseArtClasses(d.textDir, d.updateIds)
        impacts    = parseImpacts(pubConf.impactFname)
        annotLoci  = findLociBedDir(d.bedDir)

        rewriteFilterBedFiles(d.bedDir, d.tableDir, pubConf.speciesNames, \
            artDescs, artClasses, impacts, annotLoci, d.dataset)
        sortBedFiles(d.tableDir)

        fileDescs  = parseFileDescs(d.fileDescFname)
        rewriteMarkerAnnots(d.markerAnnotDir, "hgFixed", d.tableDir, fileDescs, \
            d.markerArticleFile, d.markerCountFile)
        articleDbs, annotLinks = parseBeds([d.tableDir])
        # read now from tableDir, not bedDir/protBedDir
        writeSeqTables(articleDbs, [d.seqDir, d.protSeqDir], d.tableDir, fileDescs, annotLinks)
    else:
        articleDbs, annotLinks = parseBeds([d.tableDir])

    articleDbs = addMarkerDbs(articleDbs, d.markerArticleFile)

    # reformat articles
    writeArticleTables(articleDbs, d.textDir, d.tableDir, d.updateIds)
    d.appendBatchProgress("tables")

def runIdentifierStep(d, options):
    runner = d.getRunner("identifiers")
    paramDict = {}
    paramDict["artDescFname"] = d.artDescFname
    pubAlg.mapReduce("unifyAuthors.py:GetFileDesc", d.textDir, paramDict, d.fileDescFname, \
        cleanUp=False, runTest=True, skipMap=options.skipConvert, \
        updateIds=d.updateIds, runner=runner)
    logging.info("Results written to %s" % (d.fileDescFname))
    d.appendBatchProgress("identifiers")

def dropAllTables(userTablePrefix):
    " remove all tables with current prefix "
    tablePrefix = "pubs"
    tablePrefix = tablePrefix + userTablePrefix
    dbs = pubConf.speciesNames.keys()
    dbs = [d for d in dbs if not d.startswith("nonUcsc_")]
    dbs.append("hgFixed")
    logging.info("Removing all tables with prefix %s in dbs %s" % (tablePrefix, dbs))
    logging.info("Waiting for 5 seconds before starting to delete")
    time.sleep(5)
    for db in dbs:
        logging.info("Dropping for db %s" % db)
        maxMysql.dropTablesExpr(db, tablePrefix+"%")

def overlapBeds(selectFname, inFname):
    " overlap in with select and return a dict inBedNames -> list of selectFnames "
    tempFn = join(pubConf.getTempDir(), "overlapMerged.tab")
    cmd = "overlapSelect %(selectFname)s %(inFname)s %(tempFn)s -idOutput" \
        % locals()
    maxCommon.runCommand(cmd)
    inToSel = defaultdict(set)
    for l in open(tempFn):
        if l.startswith("#"):
            continue
        fs = l.strip().split("\t")
        inId = fs[0]
        selId = fs[-1]
        inToSel[inId].add(selId)
    return inToSel

def findBedDbFilter(bedDir, dbList):
    bedFnames = glob.glob(join(bedDir, "*.bed"))
    #assert(len(bedFnames)>0)
    logging.debug("Getting all beds in %s for dbs %s" % (bedDir, dbList))

    filtBedFnames = []
    for bFn in bedFnames:
        logging.debug("checking if %s has the right db" % bFn)
        found = False
        for db in dbList:
            if db in basename(bFn):
                found = True
                break
        if not found:
            continue
        filtBedFnames.append(bFn)
    logging.info("%d bed files to process for dbs %s" % (len(filtBedFnames), dbList))
    return filtBedFnames

def concatBedCutFields(inFnames, outFile):
    """ concat all bed into some file, keep only the annotation Ids """
    for fn in inFnames:
        for line in open(fn).read().splitlines():
            fields = line.split("\t")
            #  440002039500000000:1-25,440002039500000001:0-23
            annotIds = [f.split(":")[0] for f in fields[3].split(",")]
            start, end = fields[1], fields[2]
            assert(int(start) < int(end))

            fields[3] = ",".join(annotIds)
            l = "\t".join(fields[:4])
            outFile.write(l)
            outFile.write("\n")
    outFile.flush() # cannot do close, otherwise temp file will get deleted
    logging.debug("Concatenated %d files to %s" % (len(inFnames), outFile.name))

def findLociBedDir(bedDir):
    """
    for each db, concat all beds 
    cleanup their name fields and return the loci they overlap 
    return dict annotId -> list of genes
    """
    annotToGene = defaultdict(set)
    logging.info("Getting loci for bed files in %s" % bedDir)
    dbList = pubConf.alignGenomeOrder
    for db in dbList:
        filtBedFnames = findBedDbFilter(bedDir, [db])
        tmpFh, tmpFname = pubGeneric.makeTempFile("allBeds", suffix=".bed")
        concatBedCutFields(filtBedFnames, tmpFh)
        tmpFh.flush()
        dbAnnotToGene = findLociForBeds(tmpFname, db)
        tmpFh.close() # deletes the file

        for annot, genes in dbAnnotToGene.iteritems():
            for g in genes:
                annotToGene[annot].add(g)
    return annotToGene

def findLociForBeds(bedFname, db):
    """ 
    return gene loci for annotations as a dict annotationId -> list of locusString
    """
    lociFname = join(pubConf.lociDir, db+".bed")
    if not isfile(lociFname):
        logging.info("%s not found, not annotating loci for db %s" % (lociFname, db))
    nameToGenes = overlapBeds(lociFname, bedFname)

    annotIdToGene = {}
    for name, genes in nameToGenes.iteritems():
        annotIds = name.split(",")
        for annotId in annotIds:
            annotIdToGene[annotId] = genes
    #print annotIdToGene["44000210370000000"]
    return annotIdToGene

def runStep(dataset, command, d, options):
    " run one step of the pubMap pipeline with pipeline directories in d "

    logging.info("Running step %s" % command)

    if command=="annot":
        runAnnotStep(d)

    elif command=="annotMarker":
        runAnnotStep(d, onlyMarkers=True)
    elif command=="annotSeq":
        runAnnotStep(d, onlySeq=True)


    elif command=="filter":
        cdnaDbs = [basename(path) for path in glob.glob(join(pubConf.cdnaDir, "*"))]
        dbsNotMapped = set(cdnaDbs) - set(pubConf.alignGenomeOrder)
        if len(dbsNotMapped)!=0:
            raise Exception("dbs %s have cdna data in %s but are not in pubConf" % \
                (dbsNotMapped, pubConf.cdnaDir))
        # remove duplicates & short sequence & convert to fasta
        # need to re-read d.chunkNames
        #dirs = pubMapProp.PipelineConfig(d.dataset, options.outDir)
        if not options.skipConvert:
            checkDirs = [d.seqDir, d.fastaDir, d.protSeqDir, \
                d.protSeqDir, d.protFastaDir]
            maxCommon.mustBeEmptyDir(checkDirs, makeDir=True)
            runner = d.getRunner(command)
            submitFilterJobs(runner, d.chunkNames, d.dnaAnnotDir, d.seqDir)
            submitFilterJobs(runner, d.chunkNames, d.protAnnotDir, d.protSeqDir, isProt=True)
            runner.finish(wait=True)

        # convert to fasta
        logging.info("These DBs have cDNA data in %s: %s" % (pubConf.cdnaDir, cdnaDbs))
        pubToFasta(d.seqDir, d.fastaDir, pubConf.speciesNames, pubConf.queryFaSplitSize, \
            pubConf.shortSeqCutoff)
        splitSizes = pubConf.cdnaFaSplitSizes
        pubToFasta(d.protSeqDir, d.protFastaDir, pubConf.speciesNames, \
            splitSizes, 0, forceDbs=cdnaDbs, isProt=True)
        
    elif command=="blat":
        # convert to fasta and submit blat jobs
        # make sure that directories are empty before we start this
        maxCommon.mustBeEmptyDir([d.pslDir, d.cdnaPslDir, d.protPslDir], makeDir=True)
        runner = d.getRunner(command)
        cdnaDbs = [basename(path) for path in glob.glob(join(pubConf.cdnaDir, "*"))]

        onlyDbs = options.onlyDb
        # genomes
        submitBlatJobs(runner, d.fastaDir, d.pslDir, onlyDbs, blatOptions=pubConf.seqTypeOptions)
        # cdna
        submitBlatJobs(runner, d.fastaDir, d.cdnaPslDir, onlyDbs, cdnaDir=pubConf.cdnaDir)
        # proteins
        submitBlatJobs(runner, d.protFastaDir, d.protPslDir, onlyDbs, \
            cdnaDir=pubConf.cdnaDir, blatOptions=pubConf.protBlatOptions, \
            noOocFile=True)
        runner.finish(wait=True)
        d.appendBatchProgress(command)

    elif command=="sort":
        # lift and sort the cdna and protein blat output into one file per organism-cdna 
        maxCommon.mustBeEmptyDir(d.sortBaseDir, makeDir=True)
        runner = d.getRunner(command)
        runner.maxRam = "8g"
        submitSortPslJobs(runner, "g", d.pslDir, d.pslSortedDir, pubConf.speciesNames.keys())
        cdnaDbs = [basename(path) for path in glob.glob(join(pubConf.cdnaDir, "*"))]
        submitSortPslJobs(runner, "p", d.protPslDir, d.protPslSortedDir, cdnaDbs)
        submitSortPslJobs(runner, "c", d.cdnaPslDir, d.cdnaPslSortedDir, cdnaDbs)
        runner.finish(wait=True)
        d.appendBatchProgress(command)

    elif command=="chain":
        # join all psl files from each db into one big one for all dbs, filter and re-split
        runner = d.getRunner(command)
        pslDirs = [d.pslSortedDir, d.cdnaPslSortedDir, d.protPslSortedDir]
        dbs = pubConf.speciesNames.keys()
        submitMergeSplitChain(runner, d.textDir, pslDirs, \
            d.pslSplitDir, d.bedDir, pubConf.maxDbMatchCount, dbs, d.updateIds)
        d.appendBatchProgress("chain")

    # ==== COMMANDS TO PREP OUTPUT TABLES FOR BROWSER

    elif command=="identifiers":
        # this step creates files.tab in the
        # base output directory
        runIdentifierStep(d, options)

    elif command=="tables":
        runTablesStep(d, options)

    elif command=="loci":
        # find the closest gene for each chained match
        # this is now part of the tables step!
        d = findLociBedDir(d.bedDir, ["hg19"])

    # ===== COMMANDS TO LOAD STUFF FROM THE batches/{0,1,2,3...}/tables DIRECTORIES INTO THE BROWSER
    elif command=="load":
        tablePrefix = options.tablePrefix
        runLoadStep(dataset, pubConf.speciesNames, d.markerCountsBase, \
            d.markerDirBase, tablePrefix, options.skipConvert, options.outDir)

    elif command=="dropAll":
        tablePrefix = options.tablePrefix
        dropAllTables(tablePrefix)

    elif command==("switchOver"):
        switchOver()

    # for debugging
    elif command=="_annotMarkers":
        maxCommon.mustBeEmptyDir(d.markerAnnotDir, makeDir=True)
        pubAlg.annotate("markerSearch.py:MarkerAnnotate", d.textDir, {}, \
            d.markerAnnotDir, runNow=(not options.dontRunNow), updateIds=d.updateIds, cleanUp=True)

    # ======== OTHER COMMANDS 
    elif command=="expFasta":
        annotToFasta(dataset, useExtArtId=True)

    elif command=="expCdr3":
        annotToCdr3(dataset)

    else:
        maxCommon.errAbort("unknown command: %s" % command)
            
# for recursive calls in cluster operations

if __name__ == "__main__":
    parser = optparse.OptionParser("module is calling itself on cluster machines, not meant to be used from cmdline")
    parser.add_option("-d", "--debug", dest="debug", action="store_true", help="show debug messages") 
    (options, args) = parser.parse_args()
    pubGeneric.setupLogging(__file__, options)

    command, inName, outName = args

    if command=="filterSeqFile":
        # called internally from "filter"
        filterSeqFile(inName, outName)

    elif command=="filterProtSeqFile":
        # called internally from "filter"
        filterSeqFile(inName, outName, isProt=True)

    elif command=="chainFile":
        # called by submitChainFileJobs
        chainPslToBed(inName, outName)

