import os, logging, time
from os.path import join, isfile, isdir, basename
import pubGeneric, maxTables, pubStore, pubConf, maxCommon

# name of marker counts file
MARKERCOUNTSBASE = "markerCounts.tab"
MARKERDIRBASE = "markerBeds"

def readList(path):
    " yield lines from path "
    if not isfile(path):
        return []

    identifiers = []
    for line in open(path):
        identifiers.append( line.strip())
    return identifiers


def writeList(path, identifiers):
    " write list of identifiers to file "
    logging.info("Writing %d identifiers to %s" % (len(identifiers), path))
    outFh = open(path, "w")
    for identifier in identifiers:
        outFh.write(basename(identifier)+"\n")
    outFh.close()

class PipelineConfig:
    """ a class with tons of properties to hold all directories for the pipeline 
        Most of these are relative to a BATCH (=one full run of the pipeline)
    """
    def __init__(self, dataset, outDir):
        self.markerCountsBase   = MARKERCOUNTSBASE
        self.markerDirBase      = MARKERDIRBASE
        assert(outDir!=None and outDir!="")
        self.pubMapBaseDir = outDir
        maxCommon.mustExistDir(self.pubMapBaseDir, makeDir=True)
        logging.debug("Main pipeline outdir is %s" % outDir)

        self.dataset = dataset
        if "," in dataset:
            logging.debug("comma in dataset description, deferring config")
            return

        self.textDir = pubConf.resolveTextDir(dataset)
        if self.textDir==None:
            raise Exception("dataset %s can not be resolved to a directory" % dataset)

        # base dir for dataset
        self.baseDir = join(self.pubMapBaseDir, self.dataset)

        self.batchId = self._findCurrentBatchDir()
        self.batchDir = join(self.baseDirBatches, str(self.batchId))
        self._defineBatchDirectories()

    def _findCurrentBatchDir(self):
        """ find the directory of the highest batchId that is not at "tables" yet """
        # mkdir if there is no batch dir and return "None"
        if not self._anyBatchSetup() and not isdir(self.baseDirBatches):
            logging.debug("Creating batchDir")
            os.makedirs(self.baseDirBatches)
            return None

        batchId = 0
        for nextBatchId in self._batchIds():
            logging.debug("checking batch %s" % nextBatchId)
            progressDir = join(self.baseDirBatches, str(nextBatchId), "progress")
            if self.batchIsPastStep("tables", progressDir=progressDir):
                batchId = nextBatchId
            else:
                break
        batchId = int(batchId)
        logging.info("First valid batchId is %d" % (batchId))
        return batchId

    def _defineBatchDirectories(self):
        """ 
        Set attributes for all input and output directories relative to self.batchDir 
        """
        if "," in self.dataset:
            logging.debug("comma in dataset description, deferring config")
            return

        logging.debug("Defining batch directories for %s" % self.pubMapBaseDir)
        #global baseDir
        #baseDir = self.baseDir

        # define current batch id by searching for:
        # first batch that is not at tables yet
        if not isdir(self.batchDir) or len(self._batchIds())==0:
            self.batchId  = None
            self.batchDir = None
            return
        else:
            batchDir = self.batchDir
            
        # --- now define all other directories relative to batchDir

        # pipeline progress table file
        self.progressDir = join(self.batchDir, "progress")

        # updateIds as part of this batch
        self.updateIdFile = join(self.batchDir, "updateIds.txt")
        self.updateIds = readList(self.updateIdFile)
        logging.debug("Batch is linked to these text update IDs: %s" % self.updateIds)

        # list of textfiles that were processed in batch
        self.chunkListFname = join(batchDir, "annotatedTextChunks.tab")
        self.chunkNames =  readList(self.chunkListFname)

        # directories for text annotations
        # all sequences on all articles, includes tiny seqs&duplicates
        self.dnaAnnotDir    = join(batchDir, "annots", "dna")
        self.protAnnotDir   = join(batchDir, "annots", "prot") # same for proteins
        self.markerAnnotDir = join(batchDir, "annots", "markers") # same for markers

        # tables for genome browser 
        self.tableDir     = join(batchDir, "tables")

        # non-blat files
        self.fileDescFname      = join(batchDir, "files.tab") # file descriptions for browser tables
        self.artDescFname       = join(batchDir, "articles.tab") # article descriptions for browser tables
        # articleIds associated to any marker
        self.markerArticleFile  = join(batchDir, "markerArticles.tab")
        # number of articles per marker, for base and all updates
        self.markerCountFile    = join(batchDir, MARKERCOUNTSBASE)
        # filtered marker beds, annotated with article count
        self.markerDir          = join(batchDir, MARKERDIRBASE)

        self.textConfigFname = join(batchDir, "textDir.conf") # directory where text files are stored

        # files for filter step

        filterDir = "filter"
        # unique sequences per article, dups removed
        self.seqDir           = join(batchDir, filterDir, "tab")
        self.protSeqDir       = join(batchDir, filterDir, "protTab")
        # like seqDir and protSeqDir, but in fasta format, one file per target genome
        self.fastaDir         = join(batchDir, filterDir, "fasta")
        self.protFastaDir     = join(batchDir, filterDir, "protFasta")

        # blat ouput directories
        blatDir = join(batchDir, "blat")
        self.pslDir           = join(blatDir, "genome") # blat output
        self.cdnaPslDir       = join(blatDir, "cdna") # blat output
        self.protPslDir       = join(blatDir, "prot") # blat output

        # sort output directories
        self.sortBaseDir = join(batchDir, "sort")
        self.pslSortedDir     = join(self.sortBaseDir, "genome")
        self.cdnaPslSortedDir = join(self.sortBaseDir, "cdna")
        self.protPslSortedDir = join(self.sortBaseDir, "prot")

        # chain output directories
        self.chainBaseDir = join(batchDir, "chain")
        self.pslSplitDir      = join(self.chainBaseDir, "genome")
        self.protPslSplitDir  = join(self.chainBaseDir, "prot")

        # bed step
        self.bedDir           = join(batchDir, "bed") # chained sorted blat output

    def getRunner(self, step):
        " return a runner object for the current dataset and pipelineStep"
        headNode = pubConf.stepHosts.get(step, None)
        logging.debug("Headnode for step %s is %s" % (step, headNode))
        return pubGeneric.makeClusterRunner("pubMap-"+self.dataset+"-"+step, headNode=headNode)

    def writeChunkNames(self, chunkNames):
        writeList(self.chunkListFname, chunkNames)

    def writeUpdateIds(self):
        writeList(self.updateIdFile, self.updateIds)

    def createNewBatch(self):
        " increment batch id and update the current batch id file"
        # define the dir
        if self.batchId is None:
            self.batchId = 0
        else:
            self.batchId = self.batchId+1
            logging.debug("Increasing batchId, new batchId is %s" % self.batchId)
        self.batchDir = join(self.baseDirBatches, str(self.batchId))

        # create the dir
        if isdir(self.batchDir):
            if not len(os.listdir(self.batchDir))==0:
                raise Exception("%s contains files, is this really a new run?" % self.batchDir)
        else:
            logging.debug("Creating dir %s" % self.batchDir)
            os.makedirs(self.batchDir)

        # define all other dirs
        self._defineBatchDirectories()

    def _batchIds(self):
        " return sorted list of all possible batchIds "
        subDirs = os.listdir(self.baseDirBatches)
        subDirs = [s for s in subDirs if s.isdigit()]
        subDirs = [int(s) for s in subDirs]
        subDirs.sort()
        subDirs = [str(s) for s in subDirs]
        logging.debug("Existing batch ids: %s" % subDirs)
        return subDirs

    def _anyBatchSetup(self):
        " return True if any batchDir exists "
        # no if there is not yet any batch yet at all
        logging.debug("Checking if any batchDir exists")
        self.baseDirBatches = join(self.baseDir, "batches")
        # if there is no batch dir, there is clearly no batch yet
        if not isdir(self.baseDirBatches):
            return False

        # if the batch dir does not contain any numbered directories, there is no old
        # batch yet
        if len(self._batchIds())==0:
            return False

        return True

    def completedSteps(self):
        " return list of steps completed in this batch "

        logging.debug("Checking completed steps for this batch")
        if not self._anyBatchSetup():
            return []

        if not isdir(self.progressDir):
            return False

        return os.listdir(self.progressDir)

    def batchIsPastStep(self, stepName, progressDir=None):
        """     
        check if the old batch using stepFname is at least past a certain step
        """

        if progressDir==None:
            progressDir = self.progressDir

        progressFname = join(progressDir, stepName)
        if isfile(progressFname)==True:
            logging.debug("%s exists, step was completed in this batch" % (progressFname))
            return True
        else:
            logging.debug("checking %s: this batch is not at %s yet" % (str(progressDir), stepName))
            return False
        
    def getUpdateIds(self, batchIds):
        """ 
        go over all subdirs of baseDirBatches, read the updateIds.txt files and return 
        their values. Can be limited to a given set of batchDirs.
        """
        # parse tracking file and get all updateIds
        logging.debug("Looking in %s, getting updateIds.txt files for batches %s (None=all)" % \
            (self.baseDirBatches, batchIds))

        if batchIds==None:
            batchIds = []
            for batchId in os.listdir(self.baseDirBatches):
                if not batchId.isdigit():
                    continue
                batchIds.append(batchId)

        doneUpdateIds = set()
        for batchId in batchIds:
            updFname = join(self.baseDirBatches, batchId, "updateIds.txt")
            if isfile(updFname):
                batchUpdateIds = open(updFname).read().split("\n")
                doneUpdateIds.update(batchUpdateIds)

        return doneUpdateIds

    def appendBatchProgress(self, step):
        " set flag file to signal batch progress"
        if not isdir(self.progressDir):
            os.makedirs(self.progressDir)
        logging.debug("Flagging step %s as done" % step)
        open(join(self.progressDir, step), "w")
        #if not isfile(self.stepProgressFname):
            #batchFh = open(self.stepProgressFname, "w")
            #headers = "batchId,step,date".split(",")
            #batchFh.write("\t".join(headers)+"\n")
        #else:
            #batchFh = open(self.stepProgressFname, "a")

        #row = [str(self.batchId), step, time.asctime()]
        #batchFh.write("\t".join(row)+"\n")

    def findUnannotatedUpdateIds(self):
        """ 
        find out which text-updates we have already annotated in any batch.
        Update self.updateIds with the these updateIds.
        """
        textUpdateIds = pubStore.listAllUpdateIds(self.textDir)
        batchIds = self.findBatchesAtStep("annot")
        doneUpdateIds = self.getUpdateIds(batchIds)
        self.updateIds = set(textUpdateIds).difference(doneUpdateIds)
        logging.info("text: %s, done: %s" % (textUpdateIds, doneUpdateIds))
        logging.info("Text-Updates that have not been annotated yet: %s" % self.updateIds)

    def findTableFiles(self, ignoreFilenames):
        """ find all table files across all batches in batchDir, find all files.
            create fileDict as (tableName, fileExt) -> dict of db -> list of files
            returns fileDict. 

        >>> findTableFiles("/hive/data/inside/literature/blat/miniEls", ["0"])
        """
        fileDict = {}
        logging.debug("Searching for all table files in %s" % self.baseDir)
        batchIds = self.findBatchesAtStep("tables")
        for batchId in batchIds:
            tableDir = join(self.baseDir, "batches", batchId, "tables")
            for tableFname in os.listdir(tableDir):
                tablePath = join(tableDir, tableFname)
                if tablePath in ignoreFilenames:
                    logging.debug("file %s has already been loaded, skipping" % tablePath)
                    continue
                if os.path.getsize(tablePath)==0:
                    logging.debug("file %s has 0 size, skipping" % tablePath)
                    continue
                fields = tableFname.split(".")
                if len(fields)!=3:
                    logging.debug("file %s has wrong file format (not db.table.ext), skipping " % tablePath)
                    continue
                db, table, ext = fields
                fileDict.setdefault((table, ext), {})
                fileDict[(table, ext)].setdefault(db, [])
                fileDict[(table, ext)][db].append(tablePath)

        logging.debug("Found these files: %s" % fileDict)
        return fileDict

    def findFileInAllBatchesAtStep(self, fname, step):
        " return list of file with name X in all batches that have completed a step "
        batchIds = self.findBatchesAtStep(step)
        res = []
        for batchId in batchIds:
            fname = join(self.baseDir, "batches", batchId, fname)
            if isfile(fname):
                logging.debug("Found %s" % fname)
                res.append(fname)
            else:
                logging.warn("Not found: %s" % fname)
        return res

    def findBatchesAtStep(self, step):
        """ return the list of batchIds that have run through 'step'
        """
        #batchIds = os.listdir(self.baseDirBatches)
        #batchIds = [x for x in batchIds if x.isdigit()]
        batchIds = self._batchIds()
        logging.debug("Found batches: %s" % set(batchIds))

        okBatchIds = []
        for bid in batchIds:
            #logging.debug("batchId is %s" % bid)
            progressFname = join(self.baseDirBatches, bid, "progress", step)
            #logging.debug("checking if %s exists" % progressFname)
            if isfile(progressFname):
                okBatchIds.append(bid)
        logging.debug("batchIds in %s with '%s' done: %s" % (self.baseDirBatches, step, okBatchIds))
        return okBatchIds

    def readMarkerCounts(self, counts):
        """ 
        go over all batches and get the total count for all markers in all updates
        uses markerCountFname, a table with <marker>tab<count> created by the 'tables' step
        """
        logging.info("Reading all counts from %s" % self.baseDir)
        # names of marker files
        markerCountNames = self.findFileInAllBatchesAtStep(MARKERCOUNTSBASE, "tables")
        # parse marker count files
        if len(markerCountNames)==0:
            logging.warn("No marker files found with counts")
            return counts

        for markerCountName in markerCountNames:
            counts = addCounts(counts, markerCountName) # e.g. {"rs123231":13, "TP53":5000}
        return counts

def addCounts(countDict, fname):
    " parse line of file with format <id>tab<count>, add counts to dict, return dict "
    logging.debug("Parsing %s" % fname)
    for line in open(fname):
        line = line.strip()
        fields = line.split("\t")
        if len(fields)!=2:
            logging.error("Count line %s does not contain two fields" % repr(line))
            continue
        id, count = fields
        count = int(count)
        countDict[id]+=count
    return countDict

