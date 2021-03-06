#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time
import homie
import operator as o
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from modules.homiedevice import HomieDevice
from modules.mysql import db
from modules.sendmail import Email

class ListenAll(homie.Homie):
    def _subscribe(self):
        print 'subscribing!'
        self.mqtt.subscribe(self.baseTopic+"/#", int(self.qos))
        self.subscribe_all_forced = True


class Logger(HomieDevice):

    _waiting = {}

    _comparators = {
        '==': o.eq,
        '>=': o.ge,
        '>': o.gt,
        '<=': o.le,
        '<': o.lt,
        '!=': o.ne,
    }

    def setup(self):
        self._homie.mqtt.on_message = self.mqttHandler

    def test(self, x, y, comp):
        return self._comparators[comp](x, y)

    def mqttHandler(self, client, userdata, msg, *args, **kwargs):
        parts = msg.topic.split('/')
        if parts[0] != 'devices':
            return


        properties = self._db.pq("""SELECT count(pt.propertytriggerid) as triggers, p.propertyid, p.devicestring, p.nodestring, p.propertystring,
            CONCAT(p.devicestring, '/', p.nodestring, '/', p.propertystring) as address, p.friendlyname
            FROM property p
            LEFT OUTER JOIN propertytrigger pt ON pt.propertyid = p.propertyid AND pt.active = 1
            WHERE p.propertytypeid IS NOT NULL
            GROUP BY p.propertyid""")

        for p in properties:
            if p['propertystring']:
                ptop = 'devices/{d}/{n}/{p}'.format(d=p['devicestring'], n=p['nodestring'], p=p['propertystring'])
            else:
                ptop = 'devices/{d}/{n}'.format(d=p['devicestring'], n=p['nodestring'])

            # logger.info('Topic: {topic}'.format(topic=ptop))

            if ptop == msg.topic:
                if msg.payload == 'true' or msg.payload == 'false':
                    val = 1 if msg.payload == 'true' else 0
                else:
                    val = float(msg.payload)

                logger.info('Topic: {topic} Value: {val}'.format(topic=ptop, val=val))

                self._db.pq("""INSERT INTO history (propertyid, value) VALUES (%s, %s)""", 
                    [p['propertyid'], val])
                self._db.pq("""UPDATE property set value=%s WHERE propertyid=%s""",
                    [val, p['propertyid']])


                if p['triggers'] > 0:
                    logger.info('Topic: {topic} has triggers'.format(topic=ptop))
                    triggers = self._db.pq("""SELECT value, comparator, propertyprofileid, scheduleid, schedulestatus, email, delay
                        FROM propertytrigger 
                        WHERE propertyid = %s AND active=1""", p['propertyid'])
                    for t in triggers:
                        logger.info('Testing val: {val} tval: {tval} comp: {comp}'.format(val=val, tval=t['value'], comp=t['comparator']))
                        if self.test(val, float(t['value']), t['comparator']):
                            if t['propertyprofileid'] is not None:
                                if t['delay'] > 0:
                                    self._waiting[t['propertyprofileid']] = t['delay']+time.time()
                                else:
                                    logger.info('Running profile {pid}'.format(pid=t['propertyprofileid']))
                                    self.run_profile(t['propertyprofileid'])

                            if t['scheduleid'] is not None:
                                logger.info('Changing schedule {sid} to {state}'.format(sid=t['scheduleid'], state=t['schedulestatus']))
                                self._db.pq("""UPDATE schedule SET active=%s WHERE scheduleid=%s""", [t['schedulestatus'], t['scheduleid']])

                            if t['email']:
                                em = self._db.pq("""SELECT value FROM options WHERE name='trigger_email_to'""")
                                if len(em):
                                    logger.info('Emailing {em} with update'.format(em=em[0]['value']))

                                    m = Email(em[0]['value'])
                                    m.set_message('PropertyTrigger', [p['friendlyname'], p['address'], val])
                                    m.send()

    def loopHandler(self):
        toremove = []
        now = time.time()
        for pid, delayuntil in self._waiting.iteritems():
            if now > delayuntil:
                self.run_profile(pid)
                toremove.append(pid)

        for pid in toremove:
            if pid in self._waiting:
                del self._waiting[pid]



def main():
    d = db()
    Homie = ListenAll("configs/logger.json")
    log = Logger(d, Homie)

    Homie.setFirmware("logger", "1.0.0")
    Homie.setup()

    while True:
        log.loopHandler()
        time.sleep(5)



if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Quitting.")

