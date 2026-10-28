import json
import os
import sys
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

OSLO = ZoneInfo('Europe/Oslo')
FIELDS = ('title','startDate','endDate','organizationName','facilityName','competitionTypeName','status','registrationEndDate')
OSLO_ID='52f2d358-1d50-4d41-c2ba-08d859126e0a'
AKERSHUS_ID='f3c073e8-0b7f-4de4-c2b0-08d859126e0a'
PISTOL='0beff7e2-94f7-4284-b76e-fc3e17b29e4f'

def load(path):
    p=Path(path)
    if not p.exists(): return None
    return json.loads(p.read_text(encoding='utf-8'))

def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def dt(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    return d.replace(tzinfo=OSLO) if d.tzinfo is None else d.astimezone(OSLO)

def check(data):
    if not isinstance(data, dict) or data.get('source') != 'Norges Sportsskytterforbund / SkytterAdmin':
        raise ValueError('Uventet datakilde')
    f=data.get('filters',{})
    if f.get('branch') != 'Pistol' or f.get('branchId','').lower()!=PISTOL:
        raise ValueError('Uventet gren')
    reg=f.get('regions',{})
    if {v.lower() for v in reg.values()} != {OSLO_ID,AKERSHUS_ID}:
        raise ValueError('Uventet kretsfilter')
    items=data.get('competitions')
    if not isinstance(items,list) or not items or data.get('count')!=len(items):
        raise ValueError('Tomt eller inkonsistent datasett')
    stats=data.get('statistics')
    if stats is not None and sum(stats.get(x,{}).get('count',0) for x in ('Oslo','Akershus')) != len(items):
        raise ValueError('Antall stemmer ikke med kretsstatistikk')
    try: stamp=datetime.fromisoformat(data['generatedAt'].replace('Z','+00:00'))
    except Exception as ex: raise ValueError('Ugyldig generatedAt') from ex
    now=datetime.now(OSLO)
    if abs((now-stamp.astimezone(OSLO)).total_seconds())>6*3600:
        raise ValueError('NSF-data er eldre enn seks timer eller ligger i fremtiden')
    ids=set()
    for c in items:
        if not c.get('id') or not c.get('startDate') or not isinstance(c.get('status'),int):
            raise ValueError('Stevne mangler ID/dato/status')
        if c.get('regionOrganizationId','').lower() not in {OSLO_ID,AKERSHUS_ID}:
            raise ValueError('Stevne fra feil krets')
        dt(c['startDate'])
        if c['id'] in ids: raise ValueError('Duplikat-ID')
        ids.add(c['id'])

def run():
    new=load('incoming.json')
    check(new)
    old=load('competitions.json')
    if old is not None:
        # Do not block migration from older data format; ensure previous snapshot can be indexed.
        if not isinstance(old.get('competitions'),list): raise ValueError('Ugyldig forrige snapshot')
    today=datetime.now(OSLO).date()
    end=today+timedelta(days=14)
    before={x['id']:x for x in old['competitions']} if old else {}
    after={x['id']:x for x in new['competitions']}
    first=old is None
    changes=[]
    if not first:
        for cid,c in after.items():
            if dt(c['startDate']).date()<today: continue
            prev=before.get(cid)
            if prev is None:
                changes.append({'event':'nytt_stevne','id':cid,'title':c.get('title'), 'startDate':c['startDate'], 'organizationName':c.get('organizationName')})
            else:
                diffs={k:{'before':prev.get(k),'after':c.get(k)} for k in FIELDS if prev.get(k)!=c.get(k)}
                if not diffs: continue
                event='avlyst' if prev.get('status')!=3 and c.get('status')==3 else 'endret'
                changes.append({'event':event,'id':cid,'title':c.get('title'),'startDate':c['startDate'],'organizationName':c.get('organizationName'),'fields':diffs})
        for cid,c in before.items():
            if cid not in after and dt(c['startDate']).date()>=today:
                changes.append({'event':'forsvunnet_fra_kilden','id':cid,'title':c.get('title'),'startDate':c['startDate'],'organizationName':c.get('organizationName')})
    changes.sort(key=lambda c: (c['startDate'],c['id']))
    live=sorted((c for c in after.values() if c['status']!=3 and today<=dt(c['startDate']).date()<=end),key=lambda c:(c['startDate'],c['id']))
    current={ 'source':new['source'],'generatedAt':new['generatedAt'],'windowStart':today.isoformat(),'windowEnd':end.isoformat(),'count':len(live),'competitions':live}
    events={'source':new['source'],'generatedAt':new['generatedAt'],'baselineCreated':first,'count':len(changes),'changes':changes}
    save('current.json',current)
    save('changes.json',events)
    # Replace snapshot only after all validation and delta processing.
    save('competitions.json',new)
    cells=[]
    for c in live:
        cols=(dt(c['startDate']).strftime('%d.%m.%Y %H:%M'),c.get('title'),c.get('organizationName'),c.get('facilityName'),c.get('competitionTypeName'))
        cells.append('<tr>'+''.join('<td>'+escape(str(x or 'Ikke oppgitt'))+'</td>' for x in cols)+'</tr>')
    body='\n'.join(cells) if cells else '<tr><td colspan="5">Ingen aktive stevner i perioden</td></tr>'
    page=f'''<!doctype html><html lang="no"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NSF pistolstevner</title></head><body><h1>Pistolstevner – Oslo og Akershus</h1><p>Kilde: NSF / SkytterAdmin · Sist hentet: {escape(new['generatedAt'])}</p><p>Periode: {today} til {end}. Aktive stevner: {len(live)}.</p><table border="1"><thead><tr><th>Dato/tid (NSF)</th><th>Stevne</th><th>Arrangør</th><th>Anlegg</th><th>Type</th></tr></thead><tbody>{body}</tbody></table><p><a href="current.json">14-dagersdata</a> · <a href="changes.json">Siste endringer</a></p></body></html>'''
    Path('index.html').write_text(page,encoding='utf-8')
    Path('issue.md').write_text('## NSF pistolstevner – nye endringer\n\n'+'\n'.join(f"- **{x['event']}**: {x['startDate']} – {x.get('title')} ({x.get('organizationName')}); NSF-ID `{x['id']}`" for x in changes)+'\n',encoding='utf-8')
    Path('has_changes.txt').write_text(str(len(changes)),encoding='utf-8')
    print('Validering OK.',len(new['competitions']),'totalt;',len(live),'kommende;',len(changes),'endringer. Førstegang:',first)

if __name__=='__main__':
    try: run()
    except Exception as e:
        print('FEIL:',str(e),file=sys.stderr)
        sys.exit(1)
