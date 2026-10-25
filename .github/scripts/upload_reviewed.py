"""Guard the owner-authorized Darija upload, thumbnail and API readback.

Uses the saved resumable uploader without rendering or generating narration.
A started upload is never blindly repeated. Credentials stay in memory.
"""
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
EXPECTED_CHANNEL='UCaLpAAcseB0OnnIBDOT3ujA'


def save(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def request_json(url,access=None,data=None,method=None,content_type='application/json'):
    headers={'Content-Type':content_type}
    if access:headers['Authorization']='Bearer '+access
    request=urllib.request.Request(url,data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(request,timeout=90) as response:return json.load(response)
    except urllib.error.HTTPError as error:
        # Provider errors may echo request context: report only status/reason codes.
        try:body=json.loads(error.read())
        except Exception:body={}
        reasons=[e.get('reason') for e in body.get('error',{}).get('errors',[])] if isinstance(body.get('error'),dict) else []
        if isinstance(body.get('error'),str):reasons.append(body['error'])
        raise RuntimeError(f'Google API HTTP {error.code}; reasons={reasons}') from None


def token():
    fields={n:os.environ[n].strip() for n in ['YT_CLIENT_ID','YT_CLIENT_SECRET','YT_REFRESH_TOKEN']}
    assert all(fields.values()),'Missing configured YouTube credential'
    body=urllib.parse.urlencode({'client_id':fields['YT_CLIENT_ID'],'client_secret':fields['YT_CLIENT_SECRET'],
        'refresh_token':fields['YT_REFRESH_TOKEN'],'grant_type':'refresh_token'}).encode()
    return request_json('https://oauth2.googleapis.com/token',data=body,content_type='application/x-www-form-urlencoded')['access_token']


def get(resource,access,**params):
    return request_json('https://www.googleapis.com/youtube/v3/'+resource+'?'+urllib.parse.urlencode(params),access)


def channel(access):
    result=get('channels',access,part='id,snippet,contentDetails',mine='true')
    matches=[c for c in result.get('items',[]) if c['id']==EXPECTED_CHANNEL]
    assert len(matches)==1,'Configured token does not own the expected Somu Raqami channel'
    return matches[0]


def preflight(project,access):
    c=channel(access);metadata=json.loads((project/'youtube-upload-metadata.json').read_text())
    playlist=c['contentDetails']['relatedPlaylists']['uploads']
    recent=get('playlistItems',access,part='snippet,contentDetails',playlistId=playlist,maxResults=50)
    titles={metadata['title'],'CRM ولا ERP؟ منين تبدا؟ | بالدارجة الجزائرية'}
    duplicates=[{'id':v['contentDetails']['videoId'],'title':v['snippet']['title']} for v in recent.get('items',[]) if v['snippet']['title'] in titles or ('crm' in v['snippet']['title'].lower() and 'erp' in v['snippet']['title'].lower())]
    result={'checked_at':now(),'channel_id':c['id'],'channel_title':c['snippet']['title'],
        'channel_handle':c['snippet'].get('customUrl'),'recent_uploads_checked':len(recent.get('items',[])),
        'possible_duplicate_videos':duplicates,'metadata_sha256':digest(project/'youtube-upload-metadata.json')}
    save(project/'youtube-preflight.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    assert not duplicates,'Possible existing upload found; inspect before creating another video'


def upload(project,access):
    channel(access)
    meta=json.loads((project/'youtube-upload-metadata.json').read_text())
    assert meta['publication_authorized'] and not meta['skip_upload']
    assert meta['privacy']=='unlisted' and meta['language']=='ar-DZ'
    assert len(meta['title'])<=100 and len(meta['description'])<=5000
    pre=json.loads((project/'youtube-preflight.json').read_text())
    assert not pre['possible_duplicate_videos'] and pre['metadata_sha256']==digest(project/'youtube-upload-metadata.json')
    assert (datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(pre['checked_at'])).total_seconds()<1800
    check=json.loads((project/'export-verification.json').read_text())
    assert check['audio_integrity_passed'] and check['encoded_visual_inspection_complete'] and check['full_decode_all_exports']
    delivery=json.loads((project/'delivery.json').read_text());video=Path(delivery['exports']['master.mp4']['path'])
    if not video.exists():video=project/'master.mp4'
    video_sha=digest(video);assert video_sha==delivery['exports']['master.mp4']['sha256']
    state_path=project/'youtube-upload-state.json'
    if state_path.exists():
        prior=json.loads(state_path.read_text())
        if prior.get('status')=='complete' and prior['video_sha256']==video_sha:
            print('Existing completed upload retained:',prior['video_id']);return
        raise RuntimeError('An earlier upload attempt exists; inspect its receipt rather than retrying')
    state={'status':'request_started','started_at':now(),'video_sha256':video_sha,
           'metadata_sha256':digest(project/'youtube-upload-metadata.json'),'channel_id':EXPECTED_CHANNEL}
    save(state_path,state)
    spec=importlib.util.spec_from_file_location('saved_youtube_uploader',Path(__file__).with_name('resumable_upload.py'))
    uploader=importlib.util.module_from_spec(spec);spec.loader.exec_module(uploader)
    try:result=uploader.upload(str(video),meta,access)
    except BaseException as error:
        state.update(status='failed_or_ambiguous',error_type=type(error).__name__,finished_at=now());save(state_path,state)
        raise RuntimeError('Upload did not return a completed receipt; no automatic retry') from None
    save(project/'youtube-upload-response.json',result)
    video_id=result['id']
    state.update(status='complete',video_id=video_id,completed_at=now());save(state_path,state)
    output={'id':video_id,'url':'https://youtu.be/'+video_id,'studio':'https://studio.youtube.com/video/'+video_id+'/edit',
            'asked':'unlisted','applied':result.get('status',{}).get('privacyStatus'),
            'channel_id':EXPECTED_CHANNEL,'source_master_sha256':video_sha,'uploaded_at':now()}
    save(project/'youtube.json',output);print(json.dumps(output,indent=2))


def thumbnail(project,access):
    channel(access);video=json.loads((project/'youtube.json').read_text())
    owned=get('videos',access,part='snippet,status',id=video['id'])['items'][0]
    assert owned['snippet']['channelId']==EXPECTED_CHANNEL
    image=project/'thumbnail-darija.jpg';review=json.loads((project/'thumbnail-darija-review.json').read_text())
    assert review['visually_inspected'] and review['sha256']==digest(image)
    assert image.stat().st_size<2*1024*1024
    receipt=project/'thumbnail-upload.json'
    if receipt.exists():
        prior=json.loads(receipt.read_text())
        if prior.get('success') and prior['sha256']==digest(image) and prior['video_id']==video['id']:
            print('Existing successful thumbnail application retained');return
        raise RuntimeError('Inspect earlier thumbnail receipt before replacing it')
    result=request_json('https://www.googleapis.com/upload/youtube/v3/thumbnails/set?'+urllib.parse.urlencode({'videoId':video['id'],'uploadType':'media'}),
        access,data=image.read_bytes(),method='POST',content_type='image/jpeg')
    output={'success':True,'video_id':video['id'],'channel_id':EXPECTED_CHANNEL,'sha256':digest(image),
            'bytes':image.stat().st_size,'set_at':now(),'api_response':result,'visible_thumbnail_verified':False}
    save(receipt,output);print('Custom Darija thumbnail accepted by YouTube for',video['id'])


def status(project,access):
    channel(access);video=json.loads((project/'youtube.json').read_text())
    result=get('videos',access,part='snippet,status,processingDetails,contentDetails',id=video['id'])
    assert len(result['items'])==1
    item=result['items'][0];meta=json.loads((project/'youtube-upload-metadata.json').read_text())
    assert item['snippet']['channelId']==EXPECTED_CHANNEL
    for key in ['title','description']:
        assert item['snippet'][key]==meta[key],'Applied metadata differs: '+key
    assert sorted(item['snippet'].get('tags',[]))==sorted(meta['tags']),'Applied tag values differ'
    languages={key:item['snippet'].get(key) for key in ['defaultLanguage','defaultAudioLanguage']}
    assert all(value in ['ar-DZ','ar'] for value in languages.values()),'Applied language is not Arabic'
    output={'checked_at':now(),'video_id':video['id'],'metadata_readback_passed':True,
        'expected_visibility':'unlisted','applied_visibility':item['status']['privacyStatus'],'api_video':item,
        'processing_succeeded':item.get('processingDetails',{}).get('processingStatus')=='succeeded',
        'player_chapter_ui_verified':False,'requested_language':'ar-DZ','applied_languages':languages,
        'language_normalization':'YouTube canonicalized requested ar-DZ to ar where recorded; narration remains Algerian Darija',
        'tag_values_identical_order_ignored':True,'requested_tags':meta['tags']}
    save(project/'youtube-readback.json',output)
    print(json.dumps({'id':video['id'],'metadata_readback_passed':True,'status':item['status'],
        'processing':item.get('processingDetails'),'duration':item.get('contentDetails',{}).get('duration'),
        'thumbnails':item['snippet'].get('thumbnails')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    project=Path(sys.argv[1]);mode=sys.argv[2];access=token()
    {'preflight':preflight,'upload':upload,'thumbnail':thumbnail,'status':status}[mode](project,access)
