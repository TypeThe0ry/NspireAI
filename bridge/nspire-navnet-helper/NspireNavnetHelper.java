import com.ti.eps.navnet.ConnectionHandle;
import com.ti.eps.navnet.Context;
import com.ti.eps.navnet.IntegerBox;
import com.ti.eps.navnet.NodeHandle;
import com.ti.eps.navnet.NodeNotificationListener;
import com.ti.eps.navnet.ServiceCallbackListener;
import com.ti.et.education.commproxy.INodeID;
/* Use the public client facade.  The server-side class has the same method
 * names but calls libnavnet.dylib directly; using it from this process skips
 * Student Software's RemoteNavnetServer and crashes while registering a
 * service. */
import com.ti.eps.navnet.NavNet;
import com.ti.et.navnetcommproxy.NavNetCommProxy;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;

/**
 * Small stdin/stdout adapter around TI's own macOS NavNet host library.
 *
 * The calculator-side standalone Ndless program is a NavNet client
 * (TI_NN_Connect), while this process exposes the project's private service
 * 0x5001 and keeps the connection alive.  The TI Java host accepts this
 * custom service; 0x8001 is rejected by the same host with -281.
 * stdout is deliberately line-oriented because bridge/navnet_bridge.py owns
 * framing and backend state.
 */
public final class NspireNavnetHelper {
    private static final Object IO_LOCK = new Object();
    // TI_NN_ERR_INVALID_CONNECTION.  Once the host returns this status the
    // handle is stale, not a readable channel.  Retrying the same handle
    // floods the TI server while the calculator is tearing down its peer and
    // was followed by a CX II flash/freeze.  Clear the handle and wait for a
    // fresh service callback instead.
    private static final int ERR_INVALID_CONNECTION = -257;
    // These statuses describe a transient/incomplete receive on TI's NavNet
    // implementation. They are not evidence that the service handle is dead.
    private static final int ERR_INCOMPLETE_TRANSACTION = -258;
    private static final int ERR_BUSY = -269;
    // TI's NodeNotifyCallback uses 1 for ADD and 2 for REMOVE. It is not a
    // boolean event flag; treating every non-zero value as present leaves the
    // bridge holding a dead node after a USB detach.
    private static final int NODE_EVENT_ADD = 1;
    private static volatile ConnectionHandle connection;
    private static volatile boolean stopping;
    private static volatile Thread reader;
    private static volatile Thread connectionWatcher;
    private static volatile Thread nodePoller;
    private static volatile boolean nodePresent;
    private static volatile int bootstrapServiceId;
    private static volatile ConnectionHandle bootstrapConnection;
    private static volatile Thread bootstrapThread;

    /*
     * TI's macOS NavNet 6.2 native server has a reproducible teardown crash:
     * RemoteNavnetServer.stopService(0x5001) can dereference a null service
     * slot after the USB connection has gone away.  The shell wrapper removes
     * a server created by this invocation, so the safe default is to release
     * the connection and leave stopService untouched.  Set this only for a
     * controlled TI-runtime experiment; normal bridge shutdown must not call
     * the crashing native path.
     */
    private static boolean shouldStopService() {
        return "1".equals(System.getenv().getOrDefault(
                "NSPIRE_NAVNET_STOP_SERVICE", "0"));
    }

    /* NavNetCommProxy.shutdown() calls the server-side NavNet.shutdown().
     * When the server is shared with TI Student Software, that native teardown
     * can remove the shared 0x5001 service and crash libnavnet even though the
     * helper itself is the only client being stopped.  The wrapper owns only a
     * server it created, so the safe default is to terminate this client after
     * unregistering its callback and leave the shared server alive.  Keep an
     * opt-in switch for controlled runtime experiments. */
    private static boolean shouldShutdownProxy() {
        return "1".equals(System.getenv().getOrDefault(
                "NSPIRE_NAVNET_PROXY_SHUTDOWN", "0"));
    }

    private static void armShutdownWatchdog() {
        Thread watchdog = new Thread(() -> {
            try {
                Thread.sleep(8000L);
            } catch (InterruptedException ignored) {
                return;
            }
            // TI's RMI/native shutdown occasionally logs success but leaves
            // non-daemon threads alive.  The shell wrapper separately removes
            // only a RemoteNavnetServer created by this invocation.
            Runtime.getRuntime().halt(124);
        }, "nspire-navnet-shutdown-watchdog");
        watchdog.setDaemon(true);
        watchdog.start();
    }

    private static void shutdownProxyBestEffort(NavNetCommProxy proxy) {
        Thread shutdown = new Thread(() -> {
            try {
                proxy.shutdown();
            } catch (RuntimeException ignored) {
                // The shell wrapper owns the detached RemoteNavnetServer and
                // removes only the child created by this invocation.
            }
        }, "nspire-navnet-proxy-shutdown");
        shutdown.setDaemon(true);
        shutdown.start();
        try {
            // TI's native/RMI shutdown can block indefinitely after a USB
            // connector has been loaded. Give it a small grace period, then
            // let the hard halt below keep the helper lifecycle bounded.
            shutdown.join(500L);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    private static String hex(byte[] data, int length) {
        StringBuilder out = new StringBuilder(length * 2);
        for (int i = 0; i < length; i++) {
            out.append(String.format("%02x", data[i] & 0xff));
        }
        return out.toString();
    }

    private static byte[] unhex(String value) {
        if ((value.length() & 1) != 0) throw new IllegalArgumentException("odd hex length");
        byte[] out = new byte[value.length() / 2];
        for (int i = 0; i < out.length; i++) {
            int hi = Character.digit(value.charAt(i * 2), 16);
            int lo = Character.digit(value.charAt(i * 2 + 1), 16);
            if (hi < 0 || lo < 0) throw new IllegalArgumentException("invalid hex");
            out[i] = (byte) ((hi << 4) | lo);
        }
        return out;
    }

    private static void emit(String line) {
        System.out.println(line);
        System.out.flush();
    }

    private static void write(ConnectionHandle handle, byte[] data) {
        synchronized (IO_LOCK) {
            int status = NavNet.write(handle, data, data.length);
            if (status < 0) emit("ERR NavNet.write=" + status);
        }
    }

    private static void startReader(ConnectionHandle handle) {
        if (reader != null && reader.isAlive()) return;
        reader = new Thread(() -> {
            byte[] buffer = new byte[4096];
            int transientStatus = 0;
            try {
                while (!stopping && connection == handle) {
                    IntegerBox received = new IntegerBox();
                    int status;
                    synchronized (IO_LOCK) {
                        status = NavNet.read(handle, 200L, buffer, received);
                    }
                    if (status < 0) {
                        if (stopping || connection != handle) break;
                        if (status == ERR_INVALID_CONNECTION) {
                            emit("ERR NavNet.read=" + status + "; handle invalid; waiting for callback");
                            /* Do not call NavNet.disconnect(handle): TI has
                             * already rejected the handle and its native
                             * teardown is not safe on this path.  Clearing
                             * the volatile reference also prevents the
                             * watcher from restarting a reader on the same
                             * stale handle. */
                            connection = null;
                            break;
                        }
                        if (status == ERR_INCOMPLETE_TRANSACTION || status == ERR_BUSY) {
                            if (transientStatus != status) {
                                emit("ERR NavNet.read=" + status + "; retrying");
                                transientStatus = status;
                            }
                            try {
                                Thread.sleep(50L);
                            } catch (InterruptedException interrupted) {
                                Thread.currentThread().interrupt();
                                break;
                            }
                            continue;
                        }
                        emit("ERR NavNet.read=" + status);
                        break;
                    }
                    transientStatus = 0;
                    int length = received.getValue();
                    if (length > 0) emit("RX " + hex(buffer, Math.min(length, buffer.length)));
                }
            } finally {
                // Let the watcher start a fresh reader if the service callback
                // replaced this handle while the old read was unwinding.
                if (Thread.currentThread() == reader) reader = null;
            }
        }, "nspire-navnet-reader");
        reader.setDaemon(true);
        reader.start();
    }

    private static void startConnectionWatcher() {
        if (connectionWatcher != null && connectionWatcher.isAlive()) return;
        connectionWatcher = new Thread(() -> {
            ConnectionHandle observed = null;
            while (!stopping) {
                ConnectionHandle handle = connection;
                boolean readerMissing = reader == null || !reader.isAlive();
                if (handle != null && (handle != observed || readerMissing)) {
                    observed = handle;
                    try {
                        // The service callback may run just before
                        // startService() returns, or concurrently with this
                        // watcher on a later reconnect. Never enter TI's read
                        // path until the callback has had time to unwind.
                        /* The calculator enters its first synchronous
                         * TI_NN_Read immediately after TI_NN_Connect.  Keep
                         * this handoff short so the host PING is queued before
                         * that read; the callback itself still performs no
                         * NavNet I/O. */
                        Thread.sleep(readerMissing ? 10L : 50L);
                    } catch (InterruptedException ignored) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                    if (!stopping && connection == handle &&
                            (reader == null || !reader.isAlive())) {
                        /* The calculator is the NavNet client and emits the
                         * first NSAI PING after TI_NN_Connect.  Do not write
                         * a host-first probe here: on CX II 6.2 a write made
                         * immediately after the service callback can return
                         * -257 and invalidate the fresh handle before the
                         * calculator's packet is delivered.  Start the
                         * reader first and let the normal NSAI response path
                         * provide the PONG. */
                        startReader(handle);
                    }
                }
                try {
                    Thread.sleep(50L);
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }, "nspire-navnet-connection-watcher");
        connectionWatcher.setDaemon(true);
        connectionWatcher.start();
    }

    /**
     * TI's RMI server can already know about a handheld when a new client
     * registers its callback.  In that case the callback is not guaranteed to
     * replay an ADD event, leaving the bridge at READY forever even though the
     * USB device is still 0xE022.  Poll the public connected-node list as a
     * read-only reconciliation path; this never opens a calculator channel or
     * invokes a device syscall.
     */
    private static void startNodePoller(NavNetCommProxy proxy) {
        if (nodePoller != null && nodePoller.isAlive()) return;
        nodePoller = new Thread(() -> {
            while (!stopping) {
                try {
                    INodeID[] nodes = proxy.getConnectedNodes();
                    boolean present = nodes != null && nodes.length > 0;
                    /* The callback is authoritative for removal.  During
                     * connector startup getConnectedNodes() can briefly
                     * return an empty snapshot immediately after the same
                     * node's ADD callback; treating that one read as NODE 0
                     * made a real node disappear from the bridge.  Use the
                     * poller only to reconcile positive presence and leave
                     * removal to the callback. */
                    if (present) {
                        if (!nodePresent) {
                            nodePresent = true;
                            emit("NODE 1");
                        }
                        if (bootstrapServiceId > 0 && nodes != null) {
                            try {
                                startBootstrap(proxy.getHandle(nodes[0]));
                            } catch (RuntimeException ignored) {
                                // A transient RMI node snapshot can disappear
                                // between getConnectedNodes/getHandle; the
                                // next positive poll or callback retries.
                            }
                        }
                    }
                } catch (Exception ignored) {
                    // The callback remains authoritative when the RMI server
                    // is between reconnects; retry on the next poll interval.
                }
                try {
                    Thread.sleep(500L);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }, "nspire-navnet-node-poller");
        nodePoller.setDaemon(true);
        nodePoller.start();
    }

    /*
     * Candidate-only handshake for the calculator's separate local service.
     * The historical TI test calls the calculator service from the PC before
     * the calculator connects back to the PC service.  Keep this off by
     * default: the production bridge must not open an extra NavNet channel.
     */
    private static synchronized void startBootstrap(NodeHandle node) {
        if (bootstrapServiceId <= 0 || node == null || stopping) return;
        if (bootstrapThread != null && bootstrapThread.isAlive()) return;
        bootstrapThread = new Thread(() -> {
            try {
                /* The node can be visible before the calculator reaches the
                 * Menu arm and registers 0x5002. Retry only in this explicit
                 * candidate mode, for a bounded window, so the normal helper
                 * never adds another NavNet call or timer. */
                for (int attempt = 0; attempt < 40 && !stopping; attempt++) {
                    ConnectionHandle handle = new ConnectionHandle();
                    try {
                        int status = NavNet.connect(node, bootstrapServiceId, handle);
                        emit(String.format("BOOTSTRAP CONNECT service=0x%04x attempt=%d status=%d",
                                bootstrapServiceId, attempt + 1, status));
                        if (status >= 0) {
                            bootstrapConnection = handle;
                            byte[] request = "NSAI bootstrap".getBytes(StandardCharsets.US_ASCII);
                            int writeStatus = NavNet.write(handle, request, request.length);
                            emit("BOOTSTRAP WRITE status=" + writeStatus);
                            if (writeStatus >= 0) {
                                /* NavNet.read's timeout is not a reliable
                                 * wall-clock bound on the CX II connector;
                                 * this probe used to pin the sole bootstrap
                                 * worker for tens of seconds before the
                                 * calculator had even opened 0x5002. The
                                 * calculator owns the authoritative
                                 * completion bit after it reads and answers
                                 * this request. Keep the channel briefly so
                                 * that callback can run, then let the bounded
                                 * poll retry if the page was not armed yet. */
                                emit("BOOTSTRAP awaiting calculator callback");
                                try {
                                    Thread.sleep(750L);
                                } catch (InterruptedException interrupted) {
                                    Thread.currentThread().interrupt();
                                    return;
                                }
                            }
                            return;
                        }
                    } finally {
                        if (bootstrapConnection == handle) bootstrapConnection = null;
                        try { NavNet.disconnect(handle); } catch (RuntimeException ignored) { }
                    }
                    try {
                        Thread.sleep(250L);
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                }
            } catch (RuntimeException error) {
                emit("BOOTSTRAP ERR " + error.getMessage());
            } finally {
                bootstrapThread = null;
            }
        }, "nspire-navnet-bootstrap");
        bootstrapThread.setDaemon(true);
        bootstrapThread.start();
    }

    public static void main(String[] args) throws Exception {
        final int serviceId = Integer.decode(System.getenv().getOrDefault(
                "NSPIRE_SERVICE_ID", "0x5001"));
        bootstrapServiceId = Integer.decode(System.getenv().getOrDefault(
                "NSPIRE_BOOTSTRAP_SERVICE_ID", "0"));
        String tmp = System.getProperty("java.io.tmpdir");
        String javaHome = System.getProperty("java.home") + "/bin";
        NavNetCommProxy proxy;
        try {
            /* The proxy supplies the connector path and macOS native-loader
             * setup that TI's Student Software expects.  Calling NavNet.init
             * with only "-c/-d" leaves the connector directory unset and
             * makes startService return -281. */
            int logLevel = Integer.parseInt(System.getenv().getOrDefault(
                    "NSPIRE_NAVNET_LOG_LEVEL", "0"));
            if (logLevel < 0 || logLevel > 3) {
                throw new IllegalArgumentException("NSPIRE_NAVNET_LOG_LEVEL must be 0..3");
            }
            proxy = NavNetCommProxy.init("NspireAI", tmp, logLevel, logLevel, javaHome, "");
        } catch (Exception error) {
            emit("ERR NavNetCommProxy.init=" + error);
            return;
        }
        int status;
        try {
            /* NavNet.init() establishes the RMI client.  Connector loading is
             * normally done by RemoteNavnetServer itself (and by TI Student
             * Software during its startup).  Calling loadConnectors() again
             * through a second RMI client is not idempotent on the CX II
             * macOS connector: it can initialize the same USB connector a
             * second time and abort in TI_NS_event_timedwait with a corrupted
             * pthread mutex.  The old unconditional call produced exactly
             * that host crash on 2026-09-27 while the calculator was waiting
             * for the Menu-arm path.  Keep an explicit opt-in for a controlled
             * host where the server was started without connectors; the safe
             * default is to trust the server's own initialization. */
            boolean loadConnectors = "1".equals(System.getenv().getOrDefault(
                    "NSPIRE_NAVNET_LOAD_CONNECTORS", "0"));
            if (loadConnectors) {
                int connectorStatus = NavNet.loadConnectors();
                emit("CONNECTORS status=" + connectorStatus);
                if (connectorStatus < 0) {
                    emit("ERR NavNet.loadConnectors=" + connectorStatus);
                    return;
                }
            } else {
                emit("CONNECTORS skipped=server-owned");
            }
            NavNet.registerNotifyCallback(new NodeNotificationListener() {
                @Override public void nodeNotificationCallback(NodeHandle node, int event) {
                    boolean added = event == NODE_EVENT_ADD;
                    nodePresent = added;
                    if (!added) {
                        /* A node removal invalidates every service handle
                         * associated with that handheld.  Do not leave the
                         * reader/writer pointing at the old handle while
                         * the TI connector is tearing USB down: a later
                         * SEND could otherwise enter NavNet with a stale
                         * pointer and reproduce the bridge freeze.  The
                         * reader observes the volatile clear and exits; a
                         * subsequent ADD callback installs a fresh handle. */
                        connection = null;
                        emit("DISCONNECTED reason=node-removed");
                    }
                    // Normalize TI's ADD/REMOVE values to the bridge's
                    // boolean node state; do not expose REMOVE as NODE 2.
                    emit("NODE " + (added ? 1 : 0));
                    if (added) startBootstrap(node);
                }
            });
            status = NavNet.startService(serviceId, new Context(), new ServiceCallbackListener() {
                @Override public void serviceCallback(ConnectionHandle handle, Context context) {
                    connection = handle;
                    emit(handle == null
                            ? "CONNECTED handle=null"
                            : "CONNECTED handle=0x" + Long.toHexString(handle.getCPtr()));
                    // No NavNet call is allowed from this callback. In
                    // particular, read/write here can re-enter TI's callback
                    // path and deadlock before startService() returns.
                }
            });
            if (status < 0) {
                emit("ERR NavNet.startService=" + status);
                return;
            }
            emit(String.format("READY service=0x%04x", serviceId));
            startConnectionWatcher();
            startNodePoller(proxy);
            BufferedReader input = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
            String line;
            while (!stopping && (line = input.readLine()) != null) {
                line = line.trim();
                if (line.equals("QUIT")) break;
                if (!line.startsWith("SEND ")) continue;
                ConnectionHandle handle = connection;
                if (handle == null) {
                    emit("ERR not connected");
                    continue;
                }
                try {
                    write(handle, unhex(line.substring(5).trim()));
                    emit("OK");
                } catch (RuntimeException error) {
                    emit("ERR " + error.getMessage());
                }
            }
        } finally {
            stopping = true;
            armShutdownWatchdog();
            ConnectionHandle handle = connection;
            if (handle != null) {
                try { NavNet.disconnect(handle); } catch (RuntimeException ignored) { }
            }
            ConnectionHandle bootstrap = bootstrapConnection;
            if (bootstrap != null) {
                try { NavNet.disconnect(bootstrap); } catch (RuntimeException ignored) { }
            }
            if (shouldStopService()) {
                try { NavNet.stopService(serviceId); } catch (RuntimeException ignored) { }
            }
            try { NavNet.unregisterNotifyCallback(); } catch (RuntimeException ignored) { }
            // Report the protocol endpoint as stopped before entering TI's
            // potentially blocking native teardown. The wrapper's EXIT trap
            // still removes the detached server if the best-effort thread is
            // cut short by the bounded halt.
            emit("STOPPED");
            if (shouldShutdownProxy()) {
                shutdownProxyBestEffort(proxy);
            }
            // Avoid running TI's shutdown hook a second time and guarantee
            // that no RMI client thread keeps the helper alive after cleanup.
            Runtime.getRuntime().halt(0);
        }
    }
}
