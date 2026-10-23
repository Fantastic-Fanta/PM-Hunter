// Hunt.exe's icon, from the installer's (windows/hunt.ico).
fn main() {
    println!("cargo:rerun-if-changed=../windows/hunt.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let mut res = winresource::WindowsResource::new();
        res.set_icon("../windows/hunt.ico");
        res.compile().expect("Hunt.exe's icon");
    }
}
