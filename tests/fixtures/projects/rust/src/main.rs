fn main() { println!("ok"); }

#[cfg(test)]
mod tests { #[test] fn arithmetic() { assert_eq!(2 + 3, 5); } }
