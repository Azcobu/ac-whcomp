# Compare items from AC DB to those on TBC WH.
# output items not in WH, those not in AC, and those in both
# To do: - merge shared drops [x]
#        - compare item drop chances - sort by diff? [x]
#        - add filter by item quality, so can just check blues or whatever [x]
#        - need to sort out recursive RLTs [x]
#        - add RLT numbers to WH-only and AC-WH listings [x]
#        - with dict collisions, update associated item drop chance instead of discarding

from mysql.connector import connect, Error
import requests
import json
import string
import sys

class Item:
    def __init__(self, it_id, name, lvl, droprate, quality=None, origin=None, rlt=None):
        self.it_id = it_id
        self.name = name
        self.lvl = lvl
        self.droprate = droprate
        self.quality = quality
        self.origin = origin
        self.rlt = rlt

    def __repr__(self):
        return f' {self.it_id:>6} | {self.name[:30]:<30}| {self.lvl:>3} '\
               f'| {self.droprate:>5.2f} | {self.origin} | {self.rlt}'

def open_sql_db(db_user, db_pass):
    try:
        db = connect(host = 'localhost',
                     database = 'acore_world',
                     user = db_user,
                     password = db_pass)
        if db.is_connected():
            print('Connected to AzCore database.')
    except Error as e:
        print(e)
        sys.exit(1)

    return db, db.cursor()

def get_ac_rlt_items(npc_id, item_qual, db, cursor):
    itemdict = {}
    query = ('SELECT clt.reference, clt.chance '
             'FROM `creature_loot_template` clt '
             'JOIN `creature_template` ct ON ct.lootid = clt.entry '
             f'WHERE ct.entry = {npc_id} AND clt.reference != 0')
    cursor.execute(query)
    rltlist = [x for x in cursor.fetchall()]
    #print(f'RLTs found: {rltlist}')

    while rltlist:
        rlt_id, rlt_chance = rltlist.pop()
        query = ('SELECT it.entry, it.name, it.itemlevel, it.quality '
                 'FROM `item_template` it '
                 'JOIN `reference_loot_template` rlt ON it.entry = rlt.item '
                 f'WHERE rlt.entry = {rlt_id} AND rlt.reference = 0')
        if item_qual:
            query += f' AND it.quality >= {item_qual}'
        cursor.execute(query)
        rlt_itemlist = cursor.fetchall()
        if rlt_itemlist: # some RLTS are empty of items and just hold other RLTs
            item_dropchance = round(rlt_chance / len(rlt_itemlist), 2)
            for item in rlt_itemlist:
                if item[0] not in itemdict:
                    itemdict[item[0]] = Item(item[0], item[1], item[2], item_dropchance,
                                             item[3], 'ACDB', rlt_id)
                else:
                    itemdict[item[0]].droprate += item_dropchance
                    #print(f'Dict collision - {item}')

        #now find embedded RLTs
        query = ('SELECT rlt.reference, rlt.chance '
                 'FROM `reference_loot_template` rlt '
                 f'WHERE rlt.entry = {rlt_id} AND rlt.reference != 0')
        cursor.execute(query)
        for x in cursor.fetchall():
            newrlt = (x[0], rlt_chance * x[1] / 100)
            rltlist.append(newrlt)
            #print(f'Added RLT {x[0]}')

    return itemdict

def get_ac_items(npc_id, item_qual):
    itemdict = {}
    db, cursor = open_sql_db('acore', 'password')

    query = ('SELECT it.entry, it.name, it.itemlevel, clt.chance, it.quality '
             'FROM `creature_template` ct '
             'JOIN `creature_loot_template` clt ON ct.lootid = clt.entry '
             'JOIN `item_template` it ON clt.item = it.entry '
             f'WHERE ct.entry = {npc_id} AND clt.reference = 0')
    if item_qual:
        query += f' AND it.quality >= {item_qual}'

    cursor.execute(query)
    for item in cursor.fetchall():
        itemdict[item[0]] = Item(*item, 'ACDB', 'Direct')

    #now add RLT items
    rltdict = get_ac_rlt_items(npc_id, item_qual, db, cursor)
    return {**itemdict, **rltdict}

def save_data(filename, data):
    with open(filename, 'w') as outfile:
        outfile.write(data)

def load_data(filename):
    try:
        with open(filename, 'r') as infile:
            data = infile.read()
        return data
    except Exception as err:
        print(err)

def chunk(indata):
    bracecount = 1
    chunkstr = []
    for letter in indata:
        chunkstr.append(letter)
        if letter == '{':
            bracecount += 1
        if letter == '}':
            bracecount -= 1
        if bracecount == 0:
            return  ''.join(chunkstr)
    print('Error, chunk end not found.')

def calc_droprate(parsed):
    if 'modes' in parsed:
        count = parsed['modes']['0']['count']
        outof = parsed['modes']['0']['outof']
        return round(count / outof * 100, 3)
    return 0

def parse_data(indata, item_qual):
    itemdict = {}

    srchstr = '"classs":'
    while srchstr in indata:
        left, part, indata = indata.partition(srchstr)
        itemstr = '{' + chunk(part + indata)
        try:
            parsed = json.loads(itemstr)
            droprate = calc_droprate(parsed)
            if droprate:
                newitem = Item(parsed['id'], parsed['name'], parsed['level'],
                           droprate, parsed['quality'], 'WH', '--')
                if not item_qual or newitem.quality >= item_qual:
                    itemdict[parsed['id']] = newitem
        except Exception as err:
            print(str(err) + ' - '  + itemstr[:1000])
    #wh_itemlist = sorted(itemlist, key = lambda x:x.it_id)
    return itemdict

def get_wh_items(npc_id, item_qual):
    '''
    url = 'https://tbc.wowhead.com/npc=938/'
    data = requests.get(url)
    if data.status_code == 200:
        save_data('kurzcomm.txt', data.text)
    '''
    data = load_data('kurzcomm.txt')
    return parse_data(data, item_qual) # need to add .text attribute when doing live load

def generate_merged_item(wh_it, ac_it):
    return wh_it.it_id, wh_it.name, wh_it.lvl, wh_it.droprate, ac_it.droprate,\
    abs( wh_it.droprate - ac_it.droprate), ac_it.rlt

def compare_drops(npc_id, item_qual=0):
    ac_only, wh_only, both = [], [], []
    ac_items = get_ac_items(npc_id, item_qual)
    wh_items = get_wh_items(npc_id, item_qual)

    for k, v in wh_items.items():
        if k in ac_items:
            both.append(generate_merged_item(v, ac_items[k]))
            #both.append(v)
            #both.append(ac_items[k])
            del ac_items[k]
        else:
            wh_only.append(v)
    for k, v in ac_items.items():
        ac_only.append(v)

    both = sorted(both, key = lambda x:x[5], reverse=True)
    ac_only = sorted(ac_only, key = lambda x:x.it_id)
    wh_only = sorted(wh_only, key = lambda x:x.it_id)
    return both, ac_only, wh_only

def output_data(npc_id, results, item_quality=0):
    outstr = []
    both, ac_only, wh_only = results

    if both:
        maxwidth = max([len(x[1]) for x in both]) + 1
        maxwidth = min(maxwidth, 35)
        outstr.append(' Found in both:\n')
        outstr.append(f'Item ID | Item Name {" " * (maxwidth - 10)}| iLvl| WH Drp |'
                       ' AC Drp |  Diff  | AC RLT\n')
        for item in both:
            name = f''
            outstr.append(f' {item[0]:>6} | {item[1][:35]:<{maxwidth}}| {item[2]:>3} '\
                   f'| {item[3]:>5.2f}% | {item[4]:>5.2f}% | {item[5]:>5.2f}% | {item[6]}\n')
        outstr.append(f'--------------\n{len(both)} common items found.\n')

    outstr.append('\n Found in WH only:\n')
    for item in wh_only:
        outstr.append(str(item) + '\n')
    outstr.append(f'--------------\n{len(wh_only)} WH-exclusive items found.\n')

    outstr.append('\n Found in AC only:\n')
    for item in ac_only:
        outstr.append(str(item) + '\n')
    outstr.append(f'--------------\n{len(ac_only)} AC-exclusive items found.\n')

    outstr = ''.join(outstr)
    savefilename = f'AC-WH Item Comparison For NPC {npc_id}'
    if item_quality:
        savefilename += f', Item Quality {item_quality}.txt'
    else:
        savefilename += '.txt'
    save_data(savefilename, outstr)

def main():
    npc_id = 938
    # optional, defaults to 0, where 0 = grey/all items, 1 = white, 2 = green, 3 = blue
    item_quality = 0
    results = compare_drops(npc_id, item_quality)
    output_data(npc_id, results, item_quality)

if __name__ == '__main__':
    main()