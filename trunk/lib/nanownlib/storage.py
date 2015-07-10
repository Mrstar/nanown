#-*- mode: Python;-*-

import sys
import os
import uuid
import threading
import sqlite3

import numpy

def _newid():
    return uuid.uuid4().hex


class db(threading.local):
    conn = None
    cursor = None
    _population_sizes = None
    _population_cache = None
    
    def __init__(self, path):
        exists = os.path.exists(path)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.row_factory = sqlite3.Row
        self._population_sizes = {}
        self._population_cache = {}
        
        if not exists:
            self.conn.execute(
                """CREATE TABLE meta (id BLOB PRIMARY KEY,
                                      tcpts_mean REAL,
                                      tcpts_stddev REAL,
                                      tcpts_slopes TEXT)
                """)

            self.conn.execute(
                """CREATE TABLE probes (id BLOB PRIMARY KEY,
                                        sample INTEGER,
                                        test_case TEXT,
                                        type TEXT,
                                        tc_order INTEGER,
                                        time_of_day INTEGER,
                                        local_port INTEGER,
                                        reported INTEGER,
                                        userspace_rtt INTEGER,
                                        UNIQUE (sample, test_case))
                """)

            self.conn.execute(
                """CREATE TABLE packets (id BLOB PRIMARY KEY,
                                         probe_id REFERENCES probes(id) ON DELETE CASCADE,
                                         sent INTEGER,
                                         observed INTEGER,
                                         tsval INTEGER,
                                         payload_len INTEGER,
                                         tcpseq INTEGER,
                                         tcpack INTEGER)
                """)

            self.conn.execute(
                """CREATE TABLE analysis (id BLOB PRIMARY KEY,
                                          probe_id UNIQUE REFERENCES probes(id) ON DELETE CASCADE,
                                          suspect TEXT,
                                          packet_rtt INTEGER,
                                          tsval_rtt INTEGER)
                """)

            self.conn.execute(
                """CREATE TABLE trim_analysis (id BLOB PRIMARY KEY,
                                               probe_id REFERENCES probes(id) ON DELETE CASCADE,
                                               suspect TEXT,
                                               packet_rtt INTEGER,
                                               tsval_rtt INTEGER,
                                               sent_trimmed INTEGER,
                                               rcvd_trimmed INTEGER)
                """)

            self.conn.execute(
                """CREATE TABLE classifier_results (id BLOB PRIMARY KEY,
                                                    algorithm TEXT,
                                                    params TEXT,
                                                    sample_size INTEGER,
                                                    num_trials INTEGER,
                                                    trial_type TEXT,
                                                    false_positives REAL,
                                                    false_negatives REAL)
                """)

    def __del__(self):
        if self.conn:
            self.conn.commit()
            self.conn.close()

    
    def populationSize(self, probe_type):
        if probe_type in self._population_sizes:
            return self._population_sizes[probe_type]

        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT max(c) FROM (SELECT count(sample) c FROM probes WHERE type=? GROUP BY test_case)", (probe_type,))
            self._population_sizes[probe_type] = cursor.fetchone()[0]
            return self._population_sizes[probe_type]
        except Exception as e:
            print(e)
            return 0


    def subseries(self, probe_type, unusual_case, size=None, offset=None, field='packet_rtt'):
        if (probe_type,unusual_case,field) not in self._population_cache:
            query="""
            SELECT %(field)s AS unusual_case,
                   (SELECT avg(%(field)s) FROM probes,analysis
                    WHERE analysis.probe_id=probes.id AND probes.test_case!=:unusual_case AND probes.type=:probe_type AND sample=u.sample) AS other_cases
            FROM   (SELECT probes.sample,%(field)s FROM probes,analysis 
                    WHERE analysis.probe_id=probes.id AND probes.test_case =:unusual_case AND probes.type=:probe_type) u
            """ % {"field":field}
    
            params = {"probe_type":probe_type, "unusual_case":unusual_case}
            cursor = self.conn.cursor()
            cursor.execute(query, params)
            self._population_cache[(probe_type,unusual_case,field)] = [dict(row) for row in cursor.fetchall()]

        population = self._population_cache[(probe_type,unusual_case,field)]

        if size == None or size > len(population):
            size = len(population)
        if offset == None or offset >= len(population) or offset < 0:
            offset = numpy.random.random_integers(0,len(population)-1)

        try:
            ret_val = population[offset:offset+size]
        except Exception as e:
            print(e, offset, size)
            
        if len(ret_val) < size:
            ret_val += population[0:size-len(ret_val)]
        
        return ret_val

    
    def clearCache(self):
        self._population_cache = {}

        
    def _insert(self, table, row):
        rid = _newid()
        keys = row.keys()
        columns = ','.join(keys)
        placeholders = ':'+', :'.join(keys)
        query = "INSERT INTO %s (id,%s) VALUES ('%s',%s)" % (table, columns, rid, placeholders)
        #print(row)
        self.conn.execute(query, row)
        return rid

    def addMeta(self, meta):
        ret_val = self._insert('meta', meta)
        self.conn.commit()
        return ret_val
    
    def addProbes(self, p):
        return [self._insert('probes', row) for row in p]

    def addPackets(self, pkts, window_size):
        query = ("INSERT INTO packets (id,probe_id,sent,observed,tsval,payload_len,tcpseq,tcpack)"
                 " VALUES(randomblob(16),"
                 "(SELECT id FROM probes WHERE local_port=:local_port AND :observed>time_of_day"
                 " AND :observed<time_of_day+userspace_rtt+%d" 
                 " ORDER BY time_of_day ASC LIMIT 1),"
                 ":sent,:observed,:tsval,:payload_len,:tcpseq,:tcpack)") % window_size
        self.conn.execute("PRAGMA foreign_keys = OFF;")
        self.conn.execute("CREATE INDEX IF NOT EXISTS probes_port ON probes (local_port)")
        cursor = self.conn.cursor()
        #print(query, list(pkts)[0:3])
        cursor.executemany(query, pkts)
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = ON;")

    def addAnalyses(self, analyses):
        return [self._insert('analysis', row) for row in analyses]

    def addTrimAnalyses(self, analyses):
        return [self._insert('trim_analysis', row) for row in analyses]

    def addClassifierResults(self, results):
        ret_val = self._insert('classifier_results', results)
        self.conn.commit()
        return ret_val
