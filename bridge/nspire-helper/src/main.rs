use libnspire::{Handle, PID, PID_CX2, VID};
use rusb::{Context, UsbContext};
use std::{env, fs, io::{self, BufRead, Write}, sync::mpsc, thread, time::{Duration, Instant}};

// Project-private service accepted by TI's macOS NavNet host. The raw helper
// keeps one persistent CX II USB handle and routes application packets through
// it; 0x4051 is TI's built-in Message service and is not used for NSAI frames.
const SERVICE_AI: u16 = 0x5001;
const MAGIC: &[u8; 4] = b"NSAI";

fn frame(opcode: u8, request_id: u32, conversation_id: u16, payload: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(16 + payload.len());
    out.extend_from_slice(MAGIC);
    out.push(1);
    out.push(opcode);
    out.extend_from_slice(&request_id.to_be_bytes());
    out.extend_from_slice(&conversation_id.to_be_bytes());
    out.extend_from_slice(&(payload.len() as u32).to_be_bytes());
    out.extend_from_slice(payload);
    out
}

fn decode(data: &[u8]) -> Option<(u8, u32, u16, &[u8])> {
    if data.len() < 16 || &data[..4] != MAGIC || data[4] != 1 { return None; }
    let opcode = data[5];
    let request_id = u32::from_be_bytes(data[6..10].try_into().ok()?);
    let conversation_id = u16::from_be_bytes(data[10..12].try_into().ok()?);
    let length = u32::from_be_bytes(data[12..16].try_into().ok()?) as usize;
    if length != data.len() - 16 { return None; }
    Some((opcode, request_id, conversation_id, &data[16..]))
}

fn hex_encode(data: &[u8]) -> String {
    data.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn hex_decode(value: &str) -> Result<Vec<u8>, &'static str> {
    if value.len() % 2 != 0 { return Err("odd hex length"); }
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len() / 2);
    let nibble = |c: u8| match c {
        b'0'..=b'9' => Ok(c - b'0'),
        b'a'..=b'f' => Ok(c - b'a' + 10),
        b'A'..=b'F' => Ok(c - b'A' + 10),
        _ => Err("invalid hex"),
    };
    for pair in bytes.chunks_exact(2) {
        out.push((nibble(pair[0])? << 4) | nibble(pair[1])?);
    }
    Ok(out)
}

fn write_screenshot(path: &str, image: libnspire::Image) -> Result<(), Box<dyn std::error::Error>> {
    let mut ppm = Vec::new();
    ppm.extend_from_slice(format!("P6\n{} {}\n255\n", image.width, image.height).as_bytes());
    match image.bpp {
        8 => {
            for value in image.data {
                ppm.extend_from_slice(&[value, value, value]);
            }
        }
        16 => {
            for pair in image.data.chunks_exact(2) {
                let pixel = u16::from_ne_bytes([pair[0], pair[1]]);
                let r = ((pixel & 0x1f) * 255 / 31) as u8;
                let g = (((pixel >> 5) & 0x3f) * 255 / 63) as u8;
                let b = (((pixel >> 11) & 0x1f) * 255 / 31) as u8;
                ppm.extend_from_slice(&[r, g, b]);
            }
        }
        other => return Err(format!("unsupported screenshot bpp {other}").into()),
    }
    fs::write(path, ppm)?;
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let context = Context::new()?;
    let device = context.devices()?.iter().find(|d| {
        d.device_descriptor().map(|x| x.vendor_id() == VID &&
            (x.product_id() == PID || x.product_id() == PID_CX2)).unwrap_or(false)
    }).ok_or("no TI-Nspire USB device")?;
    let handle = Handle::new(device.open()?)?;
    eprintln!("persistent USB handle open; cx2={} ready={}", handle.is_cx_ii()?, handle.cx2_ready());

    if env::args().any(|arg| arg == "--info") {
        println!("{:?}", handle.info()?);
        return Ok(());
    }

    if let Some(index) = env::args().position(|arg| arg == "--download") {
        let remote = env::args().nth(index + 1).ok_or("missing remote path")?;
        let local = env::args().nth(index + 2).ok_or("missing local path")?;
        let mut bytes = vec![0u8; 32 * 1024 * 1024];
        let size = handle.read_file(&remote, &mut bytes, &mut |_| {})?;
        if size >= bytes.len() { return Err("download limit reached".into()); }
        fs::write(local, &bytes[..size])?;
        println!("Downloaded {remote}: {size} bytes");
        return Ok(());
    }
    if let Some(index) = env::args().position(|arg| arg == "--upload") {
        let local = env::args().nth(index + 1).ok_or("missing local path")?;
        let remote = env::args().nth(index + 2).ok_or("missing remote path")?;
        handle.write_file(&remote, &fs::read(local)?, &mut |_| {})?;
        println!("Uploaded {remote}");
        return Ok(());
    }

    if let Some(index) = env::args().position(|arg| arg == "--delete-file") {
        let path = env::args().nth(index + 1).ok_or("--delete-file needs a path")?;
        handle.delete_file(&path)?;
        println!("Deleted {path}");
        return Ok(());
    }

    if let Some(index) = env::args().position(|arg| arg == "--screenshot") {
        let path = env::args().nth(index + 1).ok_or("--screenshot needs a path")?;
        let image = handle.screenshot()?;
        write_screenshot(&path, image)?;
        eprintln!("screenshot written: {path}");
        return Ok(());
    }

    handle.connect_service(SERVICE_AI)?;
    eprintln!("NavNet AI service open; sid=0x{SERVICE_AI:04x}");

    if env::args().any(|arg| arg == "--stdio") {
        /* Keep one USB event loop. libnspire's packet sequence/ack state is
         * per handle, so concurrent read/write threads can steal each
         * other's CX II acknowledgements. */
        let (command_tx, command_rx) = mpsc::channel::<String>();
        let stdin = io::stdin();
        thread::spawn(move || {
            for line in stdin.lock().lines() {
                match line {
                    Ok(line) => { if command_tx.send(line).is_err() { break; } }
                    Err(_) => break,
                }
            }
        });

        let bootstrap = frame(1, 0, 0, b"HELLO");
        let mut last_bootstrap = Instant::now() - Duration::from_secs(2);
        let mut saw_device_frame = false;
        let mut rx = vec![0u8; 4096];
        loop {
            while let Ok(command) = command_rx.try_recv() {
                let command = command.trim();
                if command == "QUIT" { return Ok(()); }
                if let Some(value) = command.strip_prefix("SEND ") {
                    match hex_decode(value) {
                        Ok(payload) => match handle.write_service_nowait(&payload) {
                            Ok(()) => {
                                println!("OK");
                                // Leave room for the CX II NNSE endpoint to
                                // enqueue the next stream packet when a long
                                // answer is fragmented into many frames.
                                thread::sleep(Duration::from_millis(4));
                            }
                            Err(error) => println!("ERR {error}"),
                        },
                        Err(error) => println!("ERR {error}"),
                    }
                    io::stdout().flush().ok();
                }
            }

            /* The Lua page may be opened after the Mac bridge. Retry the
             * first packet until the calculator has a service to accept it;
             * after any calculator-originated frame, normal traffic is live. */
            if !saw_device_frame && last_bootstrap.elapsed() >= Duration::from_secs(2) {
                match handle.write_service_nowait(&bootstrap) {
                    Ok(()) => eprintln!("TX bootstrap PING"),
                    Err(error) => eprintln!("NavNet bootstrap error: {error}"),
                }
                last_bootstrap = Instant::now();
            }

            match handle.read_service_timeout(&mut rx, 100) {
                Ok(size) if size > 0 => {
                    if rx[..size] == bootstrap[..] {
                        // A built-in echo endpoint can reflect the probe even
                        // when our Ndless application is not connected.
                        eprintln!("RX reflected bootstrap; application handshake not verified");
                        continue;
                    }
                    saw_device_frame = true;
                    println!("RX {}", hex_encode(&rx[..size]));
                    io::stdout().flush().ok();
                }
                Ok(_) | Err(libnspire::Error::Timeout) | Err(libnspire::Error::InvalidPacket) => {}
                Err(error) => eprintln!("NavNet read error: {error}; ready={}", handle.cx2_ready()),
            }
        }
    }

    if env::args().any(|arg| arg == "--ping") {
        let ping = frame(1, 1, 0, b"PING");
        handle.write_service(&ping)?;
        eprintln!("TX PING frame");
    }

    let mut rx = vec![0u8; 4096];
    loop {
        match handle.read_service(&mut rx) {
            Ok(size) => {
                let bytes = &rx[..size];
                if let Some((opcode, request_id, conversation_id, payload)) = decode(bytes) {
                    eprintln!("RX opcode={opcode} request={request_id} conversation={conversation_id} bytes={}", payload.len());
                    if opcode == 1 {
                        let pong = frame(2, request_id, conversation_id, b"PONG");
                        handle.write_service(&pong)?;
                        eprintln!("TX PONG frame");
                    }
                } else {
                    eprintln!("RX raw frame bytes={size}");
                }
                io::stdout().flush().ok();
            }
            Err(error) => {
                eprintln!("NavNet read error: {error}");
                thread::sleep(Duration::from_millis(100));
            }
        }
    }
}
