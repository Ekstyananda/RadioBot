const fetch = require('node-fetch'); // wait node 18 has fetch

async function test() {
  const res = await fetch('https://youtubei.googleapis.com/youtubei/v1/player', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'User-Agent': 'com.google.android.youtube/19.16.39 (Linux; U; Android 13)'
    },
    body: JSON.stringify({
      context: {
        client: {
          clientName: 'ANDROID',
          clientVersion: '19.16.39',
          androidSdkVersion: 33
        }
      },
      videoId: 'f11AZg7Xw40'
    })
  });
  const data = await res.json();
  const formats = data.streamingData?.adaptiveFormats || [];
  const audio = formats.find(f => f.mimeType.startsWith('audio/webm') || f.mimeType.startsWith('audio/mp4'));
  console.log(audio ? audio.url : 'No url found');
}
test();
