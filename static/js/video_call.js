/*
 * 音视频问诊（WebRTC + HTTP 轮询信令）
 *
 * 课设单机部署不引入 WebSocket / Redis：SDP Offer/Answer 与 ICE Candidate 经
 * 后端信令接口（按问诊会话隔离）轮询交换，媒体流仍走浏览器 WebRTC P2P。
 *
 * 双模式：
 *  - video：本地/远端收发摄像头 + 麦克风（画中画）；
 *  - audio：仅收发麦克风，弹窗隐藏画面，保留音频播放。
 *
 * 弹窗容器需通过 data 属性提供：
 *  - data-signal-url：信令接口地址（GET 轮询 / POST 上报）；
 *  - data-me：当前登录角色，patient 或 doctor（后端据此固定身份，前端不可伪造）；
 *  - data-peer：对端显示名（用于弹窗标题）。
 */

(function () {
    'use strict';

    var mask = document.getElementById('videoMask');
    if (!mask) return;

    var panels = document.getElementById('videoPanels');
    var localVideo = document.getElementById('localVideo');
    var remoteVideo = document.getElementById('remoteVideo');
    var statusEl = document.getElementById('videoStatus');
    var openVideoBtn = document.getElementById('openVideo');
    var openVoiceBtn = document.getElementById('openVoice');
    var closeBtn = document.getElementById('closeVideo');
    var startBtn = document.getElementById('startVideo');
    var hangupBtn = document.getElementById('hangupVideo');
    var titleEl = mask.querySelector('.video-modal-head span');

    var signalUrl = mask.getAttribute('data-signal-url');
    var me = mask.getAttribute('data-me');
    var peerLabel = mask.getAttribute('data-peer') || '';
    if (!signalUrl || !me) return;

    var RING_TIMEOUT = 30000; // 主叫等待接通超时：30 秒
    // STUN 发现反射地址；TURN 在对称 NAT 下中继媒体兜底（课设演示长期凭据）。
    var RTC_CONFIG = {
        iceServers: [
            { urls: 'stun:stun.miwifi.com:3478' },
            { urls: 'stun:stun.chat.bilibili.com:3478' },
            { urls: 'stun:stun.qq.com:3478' },
            { urls: 'stun:stun.l.google.com:19302' },
            {
                urls: 'turn:47.93.133.228:3478',
                username: 'medical',
                credential: 'Sqw5f37OPg8cDMs9T0Rdc7iJ'
            }
        ]
    };

    var localStream = null;
    var peerConn = null;
    var state = 'idle'; // idle | incoming | calling | connecting | connected
    var mode = 'video'; // video | audio
    var incomingOffer = null;
    var ringTimer = null;
    var pollTimer = null;
    var pendingIce = [];   // 远端 ICE：pc 未就绪时先缓存
    var processedIce = 0;  // 已处理的远端 ICE 游标

    function csrfToken() {
        var m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    // 调试日志：复测时在两端 Console 按 [call] 前缀定位信令/打洞断点
    function dbg(msg) {
        if (window.console && console.log) { console.log('[call]', me, msg); }
    }

    function setStatus(text) {
        if (statusEl) {
            statusEl.textContent = text;
            statusEl.classList.remove('hidden');
        }
    }

    function clearRingTimer() {
        if (ringTimer) { clearInterval(ringTimer); ringTimer = null; }
    }

    function startRingTimer(label) {
        var remain = Math.round(RING_TIMEOUT / 1000);
        setStatus(label + '… ' + remain + 's');
        ringTimer = setInterval(function () {
            remain -= 1;
            if (remain <= 0) { hangup('timeout'); return; }
            setStatus(label + '… ' + remain + 's');
        }, 1000);
    }

    function mediaConstraints() {
        return mode === 'audio'
            ? { video: false, audio: true }
            : { video: true, audio: true };
    }

    // 媒体采集：优先标准 mediaDevices，回退旧前缀；都不可用（非安全上下文）时标记 insecure。
    function requestMedia(constraints) {
        if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
            return navigator.mediaDevices.getUserMedia(constraints);
        }
        var legacy = navigator.getUserMedia || navigator.webkitGetUserMedia || navigator.mozGetUserMedia;
        if (legacy) {
            return new Promise(function (resolve, reject) {
                legacy.call(navigator, constraints, resolve, reject);
            });
        }
        var err = new Error('当前环境禁止调用摄像头/麦克风（非安全上下文）');
        err.insecure = true;
        return Promise.reject(err);
    }

    function mediaErrorMessage(err) {
        if (err && err.insecure) {
            return '当前页面不是 HTTPS、也不是本机 localhost，浏览器禁止开启摄像头/麦克风。\n\n'
                + '实测办法：在本机用 http://127.0.0.1:8000 访问（localhost 属于安全上下文）；'
                + '若必须远程公网访问，需为服务器配置 HTTPS 证书后再试。';
        }
        return '无法开启摄像头/麦克风：' + (err && err.message ? err.message : '未知错误');
    }

    function applyModeUi() {
        var audio = mode === 'audio';
        if (panels) panels.classList.toggle('is-audio', audio);
        if (titleEl) titleEl.textContent = (audio ? '语音问诊' : '视频问诊') + ' · ' + peerLabel;
        if (startBtn) {
            startBtn.textContent = state === 'incoming' ? '接听' : '发起呼叫';
        }
    }

    function stopLocalStream() {
        if (localStream) {
            localStream.getTracks().forEach(function (t) { t.stop(); });
            localStream = null;
        }
        if (localVideo) localVideo.srcObject = null;
        if (remoteVideo) remoteVideo.srcObject = null;
        if (panels) panels.classList.remove('has-local');
    }

    // 挂载本地预览流：绑定 srcObject、显示小窗、显式 play（不依赖 autoplay），并记录视频轨状态便于排查。
    function attachLocalStream() {
        if (!localStream) return;
        if (localVideo) {
            localVideo.srcObject = localStream;
            localVideo.muted = true;
            var playPromise = localVideo.play();
            if (playPromise && playPromise.catch) {
                playPromise.catch(function (err) { dbg('本地预览 play 被拦截：' + (err && err.message ? err.message : err)); });
            }
        }
        if (mode === 'video' && panels) panels.classList.add('has-local');
        var videoTrack = localStream.getVideoTracks()[0];
        if (videoTrack) {
            dbg('本地视频轨：' + videoTrack.label + ' state=' + videoTrack.readyState);
            videoTrack.onended = function () { dbg('本地视频轨已 ended'); };
            videoTrack.onunmute = function () { dbg('本地视频轨 unmute（开始出帧）'); };
            videoTrack.onmute = function () { dbg('本地视频轨 mute（暂停出帧）'); };
        } else if (mode === 'video') {
            dbg('警告：本地流未包含视频轨（仅音频）');
        }
    }

    function postSignal(payload) {
        return fetch(signalUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken(),
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify(payload)
        }).then(function (r) {
            if (!r.ok) { dbg('signal HTTP ' + r.status + ' 上报 ' + payload.type); }
            return r;
        }).catch(function (err) {
            dbg('signal 上报失败 ' + payload.type + '：' + (err && err.message ? err.message : err));
        });
    }

    function createPeerConnection() {
        var pc = new RTCPeerConnection(RTC_CONFIG);

        pc.onicecandidate = function (event) {
            if (event.candidate) {
                postSignal({ type: 'ice', candidate: event.candidate.toJSON() });
            }
        };

        pc.ontrack = function (event) {
            if (remoteVideo) {
                remoteVideo.srcObject = event.streams[0];
                var p = remoteVideo.play();
                if (p && p.catch) p.catch(function () {});
            }
            if (state !== 'connected') {
                state = 'connected';
                clearRingTimer();
                setStatus('通话中');
            }
        };

        pc.oniceconnectionstatechange = function () {
            if (!pc) return;
            dbg('ICE 状态：' + pc.iceConnectionState);
            if (pc.iceConnectionState === 'failed') {
                setStatus('打洞失败：跨网络可能需要 TURN 中继');
            } else if (pc.iceConnectionState === 'disconnected') {
                setStatus('连接已断开');
            }
        };
        return pc;
    }

    function addRemoteIce(candidates) {
        if (!peerConn) {
            pendingIce = pendingIce.concat(candidates);
            return;
        }
        candidates.forEach(function (c) {
            peerConn.addIceCandidate(new RTCIceCandidate(c)).catch(function () {});
        });
    }

    function resetCall() {
        stopLocalStream();
        if (peerConn) { peerConn.close(); peerConn = null; }
        clearRingTimer();
        state = 'idle';
        incomingOffer = null;
        pendingIce = [];
        processedIce = 0;
        if (startBtn) startBtn.disabled = false;
    }

    function openModal(openMode) {
        resetCall();
        mode = openMode || mode;
        applyModeUi();
        setStatus('等待发起呼叫');
        mask.hidden = false;
    }

    // 主叫：建立房间 → 本地采集 → 发 Offer
    async function startCall() {
        if (state !== 'idle') return;
        try {
            localStream = await requestMedia(mediaConstraints());
            attachLocalStream();

            peerConn = createPeerConnection();
            localStream.getTracks().forEach(function (track) {
                peerConn.addTrack(track, localStream);
            });

            await postSignal({ type: 'call', mode: mode });
            var offer = await peerConn.createOffer();
            await peerConn.setLocalDescription(offer);
            await postSignal({ type: 'offer', mode: mode, sdp: offer.sdp });
            dbg('Offer 已上报，等待 Answer');
            state = 'calling';
            if (startBtn) startBtn.disabled = true;
            startRingTimer('呼叫中');
        } catch (err) {
            console.error('[call] 发起呼叫失败：', err);
            alert(mediaErrorMessage(err));
            resetCall();
        }
    }

    // 被叫：接受来电 → 回 Answer
    async function acceptCall(data) {
        var offer = data.offer;
        if (!offer) return;
        mode = data.mode || mode;
        state = 'ringing';
        applyModeUi();
        setStatus('接听中…');
        try {
            localStream = await requestMedia(mediaConstraints());
            attachLocalStream();

            peerConn = createPeerConnection();
            localStream.getTracks().forEach(function (track) {
                peerConn.addTrack(track, localStream);
            });

            await peerConn.setRemoteDescription({ type: 'offer', sdp: offer.sdp });
            var cached = pendingIce; pendingIce = [];
            cached.forEach(function (c) {
                peerConn.addIceCandidate(new RTCIceCandidate(c)).catch(function () {});
            });

            var answer = await peerConn.createAnswer();
            await peerConn.setLocalDescription(answer);
            await postSignal({ type: 'answer', sdp: answer.sdp });
            dbg('Answer 已上报');
            if (startBtn) startBtn.disabled = true;
        } catch (err) {
            console.error('[call] 接听失败：', err);
            resetCall();
            setStatus(mediaErrorMessage(err));
        }
    }

    function onIncoming(data) {
        incomingOffer = data;
        state = 'incoming';
        mode = data.mode || mode;
        applyModeUi();
        setStatus('对方呼叫中…');
        mask.hidden = false;
        if (startBtn) startBtn.disabled = false;
    }

    function pollRoom() {
        fetch(signalUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data) return;

                if (data.status === 'ended') { hangup('remote'); return; }

                // 被叫：发现对端 Offer，进入来电态
                if (data.offer && state === 'idle') {
                    dbg('收到对端 Offer');
                    onIncoming(data);
                }
                // 主叫：收到 Answer，SDP 交换完成即进入“连接中”，媒体轨到达后再转通话中
                if (data.answer && peerConn && peerConn.signalingState !== 'stable') {
                    dbg('收到对端 Answer，建立远端描述');
                    peerConn.setRemoteDescription(
                        { type: 'answer', sdp: data.answer.sdp })
                        .then(function () {
                            if (state === 'calling') {
                                state = 'connecting';
                                clearRingTimer();
                                setStatus('连接中…');
                            }
                        })
                        .catch(function (err) { console.error('[call] 设置远端 Answer 失败：', err); });
                }
                // ICE 增量交换
                if (Array.isArray(data.ice) && data.ice.length > processedIce) {
                    var fresh = data.ice.slice(processedIce);
                    processedIce = data.ice.length;
                    addRemoteIce(fresh);
                }
            })
            .catch(function (err) { dbg('信令轮询异常：' + (err && err.message ? err.message : err)); })
            // 无论成功/异常都重排轮询，避免信令轮询静默停摆
            .finally(function () { schedule(state === 'connected' ? 3000 : 1500); });
    }

    function schedule(delay) {
        pollTimer = setTimeout(pollRoom, delay);
    }

    // reason: timeout 主叫超时；remote 对端挂断；其余为本地主动挂断
    function hangup(reason) {
        postSignal({ type: 'hangup' });
        resetCall();
        setStatus('等待发起呼叫');
        mask.hidden = true;
        if (reason === 'timeout') {
            alert('呼叫超时，对方未接通，已自动挂断。');
        } else if (reason === 'remote') {
            alert('对方已挂断。');
        }
    }

    if (openVideoBtn) openVideoBtn.addEventListener('click', function () { openModal('video'); });
    if (openVoiceBtn) openVoiceBtn.addEventListener('click', function () { openModal('audio'); });
    if (closeBtn) closeBtn.addEventListener('click', function () { hangup(); });
    if (hangupBtn) hangupBtn.addEventListener('click', function () { hangup(); });
    if (startBtn) startBtn.addEventListener('click', function () {
        if (incomingOffer) { acceptCall(incomingOffer); } else { startCall(); }
    });
    mask.addEventListener('click', function (event) {
        if (event.target === mask) hangup();
    });

    // 常驻轮询：弹窗关闭也监听来电，被叫可自动收到呼叫提示
    schedule(1200);
})();
