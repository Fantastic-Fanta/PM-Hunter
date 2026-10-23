//! The hunter, the same on every platform (Mac, Windows, Android): it reads
//! each wild encounter in the Roblox Pokemon game, runs from the ones not
//! wanted, catches the ones worth a ball, and pings Discord and holds the
//! battle for you when a wanted one shows up. What it needs from the computer
//! or phone (the screen, clicks or taps, text recognition) is a Device.

pub mod battle;
pub mod calibrate;
pub mod config;
pub mod device;
pub mod discord;
pub mod hunter;
pub mod layout;
pub mod menu;
pub mod out;
pub mod picture;
pub mod px;
pub mod reader;
pub mod rules;
pub mod store;
pub mod tools;
pub mod util;
pub mod vision;
pub mod web;

pub use config::Config;
pub use device::{Device, Sound, Traits};
pub use hunter::{start, Control, Halt, Hunt, Options};
pub use out::{Console, DebugChannel, Out, Sink};
pub use px::{Frame, IBox, Pt};
pub use reader::TextHit;
pub use store::Store;
