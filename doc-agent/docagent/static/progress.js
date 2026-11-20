/* Shared real-byte upload progress and measured server-stage progress. */
(function () {
  function duration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return 'Estimating…';
    if (seconds < 1) return 'Less than a second';
    const n = Math.ceil(seconds);
    return n < 60 ? n + 's' : Math.floor(n / 60) + 'm ' + n % 60 + 's';
  }
  function meter(parent, title) {
    const box = document.createElement('div');
    box.style.cssText = 'padding:12px 0;';
    const name = document.createElement('strong'); name.textContent = title;
    const bar = document.createElement('progress'); bar.max = 100;
    bar.setAttribute('aria-label', title);
    bar.style.cssText = 'display:block;width:100%;height:18px;accent-color:#2f6fdb;margin:8px 0;';
    const label = document.createElement('div'); label.setAttribute('role', 'status');
    box.append(name, bar, label); parent.append(box);
    return { update(text, percent = null) {
      if (percent === null) bar.removeAttribute('value'); else bar.value = percent;
      label.textContent = text;
    }};
  }
  function upload(url, file, parent) {
    const view = meter(parent, file.webkitRelativePath || file.name);
    view.update('Uploading · 0% · Time left: estimating…', 0);
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      let started = performance.now();
      xhr.open('POST', url);
      xhr.upload.onprogress = event => {
        const elapsed = (performance.now() - started) / 1000;
        if (!event.lengthComputable) { view.update('Uploading · Time left: estimating…'); return; }
        const percent = Math.min(100, event.loaded / event.total * 100);
        const left = elapsed >= 0.5 && event.loaded > 0 ? (event.total - event.loaded) * elapsed / event.loaded : NaN;
        view.update('Uploading · ' + Math.floor(percent) + '% · Estimated upload time left: ' + duration(left), percent);
      };
      xhr.upload.onload = () => view.update('Upload transferred · Server reading / processing · Time left: estimating…');
      xhr.onload = () => {
        let result; try { result = JSON.parse(xhr.responseText); } catch { result = {}; }
        if (xhr.status >= 200 && xhr.status < 300) {
          view.update('Upload accepted · 100% · Upload time left: 0s', 100); resolve(result);
        } else {
          const message = typeof result.detail === 'string' ? result.detail : 'Upload or processing failed';
          view.update('Failed · ' + message + ' · Select this file again to retry', 0); reject(Error(message));
        }
      };
      xhr.onerror = xhr.onabort = () => { view.update('Connection lost · Upload state unknown; reselect the file to retry', 0); reject(Error('Upload connection lost')); };
      const body = new FormData(); body.append('file', file, file.webkitRelativePath || file.name); xhr.send(body);
    });
  }
  function processing(parent, row) {
    const view = meter(parent, 'Server processing');
    if (row.status === 'Completed') { view.update('Completed · 100% · Time left: 0s', 100); return; }
    if (row.status === 'Failed') { view.update('Failed · Time left unavailable', 0); return; }
    if (row.status === 'Queued') { view.update('Queued · Time left: estimating…'); return; }
    if (row.total > 0 && row.done != null) {
      const percent = Math.floor(row.done / row.total * 100);
      const phase = row.status === 'Reading / OCR' ? 'Source preparation' : row.status;
      const left = row.stage_elapsed > 0 && row.done > 0 ? row.stage_elapsed * (row.total - row.done) / row.done : NaN;
      view.update(phase + ' · ' + percent + '% of stage · ' + row.done + '/' + row.total + ' · Estimated stage time left: ' + duration(left) + ' · Overall time left: estimating…', percent);
    } else view.update(row.status + ' · Progress and time left: estimating…');
  }
  window.DocProgress = {duration, meter, upload, processing};
})();
