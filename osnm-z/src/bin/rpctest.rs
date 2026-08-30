#![allow(unused)]
// rpctest — reproduce the HTTP error with the exact reqwest client.
use std::time::Duration;
use std::error::Error;

fn main() -> Result<(), Box<dyn Error>> {
    let url: reqwest::Url = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "https://rpc.nodeflare.app/robinhood/public".into())
        .parse()?;

    let rt = tokio::runtime::Runtime::new()?;
    rt.block_on(async {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_millis(15_000))
            .pool_idle_timeout(None)
            .pool_max_idle_per_host(2)
            .tcp_keepalive(Duration::from_mins(1))
            .http2_adaptive_window(true)
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .unwrap();

        let body = serde_json::json!({"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]});
        match client.post(url.clone()).json(&body).send().await {
            Ok(resp) => {
                println!("status={} len={:?}", resp.status(), resp.content_length());
                let text = resp.text().await.unwrap_or_default();
                println!("body={}", &text[..text.len().min(400)]);
            }
            Err(e) => println!("SEND ERROR: {:?}\n  source: {:?}\n  kind: {}", e, e.source(), if e.is_connect() {"connect"} else if e.is_timeout() {"timeout"} else {"other"}),
        }
    });
    Ok(())
}
