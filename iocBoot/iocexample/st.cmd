#!../../bin/linux-x86_64-debug/feedioc

dbLoadDatabase("../../dbd/feedioc.dbd",0,0)
feedioc_registerRecordDeviceDriver(pdbbase) 

var(feedNumInFlight, 8)

epicsEnvSet("EPICS_DB_INCLUDE_PATH", ".:../../db")

# The file "example.substitutions" was generated by running:
#  ./leep.py <hostname/ip> example.substitutions
#
# By default all records connected to registers have SCAN=Passive
dbLoadTemplate("example.substitutions","P=TST:,NAME=device,REGSCAN=Passive,DEBUG=0")

# LLRF waveform acquisition demo
#dbLoadTemplate("acquire.substitutions", "P=TST:acq:,NAME=device,TPRO=0")

iocInit()

dbl > records.dbl

dbpf "TST:ctrl:Addr-SP" "127.0.0.1"
