"""
Copyright (c) 2021, Saurabh Pujari
All rights reserved.

This source code is licensed under the BSD-style license found in the LICENSE file in the root directory of this source tree.
"""
import os
from .elarautil import Util
from .lru import LRU, Cache_obj
from .status import Status


def is_pos(val):
    return isinstance(val, int) and val > 0


class Elara:

    from .strings import setnx, append, getset, mget, mset, msetnx, slen
    from .lists import (
        lnew,
        lpush,
        lextend,
        lindex,
        lrange,
        lrem,
        lpop,
        llen,
        lappend,
        lexists,
        linsert,
    )
    from .hashtables import hnew, hadd, haddt, hget, hpop, hkeys, hvals, hexists, hmerge
    from .shared import (
        retmem,
        retdb,
        retkey,
        commit,
        exportdb,
        exportkeys,
        exportmem,
        securedb,
        updatekey,
    )

    def __init__(self, path, commitdb, key_path=None, cache_param=None):
        self.path = os.path.expanduser(path)
        self.commitdb = commitdb

        if cache_param == None:
            self.lru = LRU()
            self.max_age = None
            self.cull_freq = 20  # Delete 20% by default
        else:  # exe_cache() mode
            if "max_age" in cache_param and "max_size" in cache_param:
                if is_pos(cache_param["max_age"]) and is_pos(cache_param["max_size"]):
                    self.lru = LRU(cache_param["max_size"])
                    self.max_age = cache_param["max_age"]
                else:
                    raise Exception
            elif "max_age" in cache_param:
                if is_pos(cache_param["max_age"]):
                    self.max_age = cache_param["max_age"]
                    self.lru = LRU()
                else:
                    raise Exception
            elif "max_size" in cache_param:
                if is_pos(cache_param["max_size"]):
                    self.lru = LRU(cache_param["max_size"])
                    self.max_age = None
                else:
                    raise Exception
            if "cull_freq" in cache_param:
                if is_pos(cache_param["cull_freq"]) and cache_param["cull_freq"] <= 100:
                    self.cull_freq = cache_param["cull_freq"]
                else:
                    raise Exception
            else:
                self.cull_freq = 20

        # this is in place to prevent opening incompatible databases between versions of the storage format
        self.db_format_version = 0x0001

        # Since key file is generated first, invalid token error for pre existing open dbs
        # Load the database key
        if not key_path == None:
            new_key_path = os.path.expanduser(key_path)
            if os.path.exists(new_key_path):
                file = open(new_key_path, "rb")
                self.key = file.read()
                file.close()
            else:
                self.key = None
        else:
            self.key = None

        # Load the data
        if os.path.exists(path):
            self._load()
        else:
            self.db = {}

    def _load(self):
        if self.key:
            self.db = Util.read_and_decrypt(self)
            self.lru._load(self.db, self.max_age)
        else:
            self.db = Util.read_plain_db(self)
            self.lru._load(self.db, self.max_age)

    def _dump(self):
        if self.key:
            Util.encrypt_and_store(self)  # Enclose in try-catch
        else:
            Util.store_plain_db(self)

    def _autocommit(self):
        if self.commitdb:
            self._dump()

    # remove a list of keys from db only
    def _remkeys_db_only(self, keys=[]):
        for key in keys:
            del self.db[key]
        self._autocommit()

    # Take max_age or self.max_age
    def set(self, key, value, max_age=None):
        if isinstance(key, str):
            if max_age == None:
                cache_obj = Cache_obj(key, self.max_age)
            elif max_age == "i":
                cache_obj = Cache_obj(key, None)
            else:
                if is_pos(max_age):
                    cache_obj = Cache_obj(key, max_age)
                else:
                    raise Exception

            # this is for when a key is being overwritten by set
            if self.exists(key):
                self.rem(key)
                self.lru.push(cache_obj)
            elif self.lru.push(cache_obj) == Status.FULL:
                self.cull(self.cull_freq)
                self.lru.push(cache_obj)

            self.db[key] = value
            self._autocommit()
            return True
        else:
            raise Exception

    def get(self, key):
        try:
            res = self.lru.touch(key)
            if res == Status.EXPIRED:
                del self.db[key]
                return None
            elif res == Status.NOTFOUND:
                return None
            else:
                return self.db[key]
        except KeyError:
            return None

    def rem(self, key):
        if self.lru.rem_key(key) == False:
            raise Exception
        del self.db[key]
        self._autocommit()
        return True

    def remkeys(self, keys=[]):
        for key in keys:
            if self.exists(key):
                self.rem(key)
            self._autocommit()

    def clear(self):
        self.lru.clear()
        self.db = {}
        self._autocommit()
        return True

    def exists(self, key):
        res = self.lru.touch(key)
        if res == Status.EXPIRED:
            del self.db[key]
            self._autocommit()
            return False
        elif res == Status.NOTFOUND:
            return False
        return key in self.db

    def cull(self, percentage=20):
        if 0 <= percentage <= 100:
            count = int((percentage / 100) * (self.lru.size))
            # print("final count", count)

            if count == 0 and (percentage > 0 and self.lru.size > 0):
                cache_obj = self.lru.pop()
                del self.db[cache_obj.key]
            else:
                for i in range(0, count):
                    cache_obj = self.lru.pop()
                    if cache_obj == False:
                        break
                    del self.db[cache_obj.key]

            self._autocommit()
            return True
        else:
            return False

    def getkeys(self):
        deleted_keys, cache = self.lru.all()
        self._remkeys_db_only(deleted_keys)

        keys = []
        for cache_obj in cache:
            keys.append(cache_obj.key)

        return keys

    def numkeys(self):
        deleted_keys, cache = self.lru.all()
        self._remkeys_db_only(deleted_keys)

        return len(cache)

    def incr(self, key, val=1):
        if self.exists(key):
            data = self.get(key)
            if isinstance(data, (int, float)):
                data += val
                data = round(data, 3)
                self.set(key, data)
            else:
                return False
        else:
            return False

    def decr(self, key, val=1):
        if self.exists(key):
            data = self.get(key)
            if isinstance(data, (int, float)):
                data -= val
                data = round(data, 3)
                self.set(key, data)
            else:
                return False
        else:
            return False
