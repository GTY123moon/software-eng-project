/*
 * 视频问诊（课设演示版）
 *
 * 当前能力：
 *  - 画中画布局：大窗为对方画面，右下角小窗为本地摄像头预览；
 *  - 发起呼叫后进入等待态，超时未接通自动挂断；
 *  - 支持主动挂断（挂断按钮 / 右上角关闭 / 点击遮罩），统一释放本地流。
 *
 * WebRTC 骨架已预留：
 *  - RTCPeerConnection + 公共 STUN 配置；
 *  - ontrack 把对端流挂到大窗 remoteVideo。
 * 真正的双方实时通话还需要：
 *  1. 信令通道（WebSocket 或轮询）交换 SDP Offer/Answer 与 ICE Candidate；
 *  2. 跨网络场景下的 TURN 中继服务；
 *  3. 医生端页面配合同一套信令流程。
 * 因此本文件在演示环境只做本地预览，不发起真实对端呼叫。
 */

(function () {
    'use strict';

    var RING_TIMEOUT = 30000; // 呼叫等待超时：30 秒未接通自动挂断

    var mask = document.getElementById('videoMask');
    if (!mask) return;

    var panels = document.getElementById('videoPanels');
    var localVideo = document.getElementById('localVideo');
    var remoteVideo = document.getElementById('remoteVideo');
    var statusEl = document.getElementById('videoStatus');
    var openBtn = document.getElementById('openVideo');
    var closeBtn = document.getElementById('closeVideo');
    var startBtn = document.getElementById('startVideo');
    var hangupBtn = document.getElementById('hangupVideo');

    var localStream = null;
    var peerConn = null;
    var state = 'idle'; // idle | calling | connected
    var ringTimer = null;
    var remain = 0;

    // 公共免费 STUN，用于打洞；生产环境需替换为自有/托管 TURN。
    var RTC_CONFIG = {
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
    };

    function createPeerConnection() {
        var pc = new RTCPeerConnection(RTC_CONFIG);
        pc.onicecandidate = function (event) {
            // TODO: 经信令通道把 event.candidate 发给对方
            if (event.candidate) console.log('ICE candidate', event.candidate);
        };
        pc.ontrack = function (event) {
            // 收到对端流视为接通
            if (state !== 'connected') {
                state = 'connected';
                clearRingTimer();
                remoteVideo.srcObject = event.streams[0];
                if (statusEl) statusEl.classList.add('hidden');
            }
        };
        return pc;
    }

    function setStatus(text) {
        if (statusEl) {
            statusEl.textContent = text;
            statusEl.classList.remove('hidden');
        }
    }

    function clearRingTimer() {
        if (ringTimer) {
            clearInterval(ringTimer);
            ringTimer = null;
        }
    }

    function startRingTimer() {
        remain = Math.round(RING_TIMEOUT / 1000);
        setStatus('呼叫中… ' + remain + 's');
        ringTimer = setInterval(function () {
            remain -= 1;
            if (remain <= 0) {
                hangup('timeout');
                return;
            }
            setStatus('呼叫中… ' + remain + 's');
        }, 1000);
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

    // reason: 'timeout' 表示超时未接通主动挂断；其余为用户主动挂断/关闭
    function hangup(reason) {
        clearRingTimer();
        stopLocalStream();
        if (peerConn) {
            peerConn.close();
            peerConn = null;
        }
        state = 'idle';
        if (startBtn) startBtn.disabled = false;
        setStatus('等待发起呼叫');
        mask.hidden = true;

        if (reason === 'timeout') {
            alert('呼叫超时，对方未接通，已自动挂断。');
        }
    }

    function openModal() {
        // 打开弹窗时复位到空闲态
        stopLocalStream();
        if (peerConn) {
            peerConn.close();
            peerConn = null;
        }
        clearRingTimer();
        state = 'idle';
        if (startBtn) startBtn.disabled = false;
        setStatus('等待发起呼叫');
        mask.hidden = false;
    }

    async function startCall() {
        if (state !== 'idle') return;
        try {
            localStream = await navigator.mediaDevices.getUserMedia({
                video: true,
                audio: true
            });
            localVideo.srcObject = localStream;
            if (panels) panels.classList.add('has-local');

            // 真实呼叫时：把本地轨道加入 peerConn，并经信令发 Offer。
            peerConn = createPeerConnection();
            localStream.getTracks().forEach(function (track) {
                peerConn.addTrack(track, localStream);
            });

            state = 'calling';
            if (startBtn) startBtn.disabled = true;
            if (hangupBtn) hangupBtn.disabled = false;
            startRingTimer();
        } catch (err) {
            alert('无法开启摄像头/麦克风：' + err.message + '（演示环境可忽略）');
            stopLocalStream();
        }
    }

    openBtn.addEventListener('click', openModal);
    closeBtn.addEventListener('click', function () { hangup(); });
    hangupBtn.addEventListener('click', function () { hangup(); });
    startBtn.addEventListener('click', startCall);
    mask.addEventListener('click', function (event) {
        if (event.target === mask) hangup();
    });
})();
