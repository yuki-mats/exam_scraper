# T027 実route probe

T025で`unsafe_model_override`となったq3・q4を、新しい隔離copyで実Ollama `qwen3:14b`と実Codex App Serverへ直列投入した。

両問とも独立レビュー1・2はCodex `gpt-5.6-luna`で解決し、問題形式候補はローカル`qwen3:14b`のprimaryでvalidatedとなった。q4の解説候補はlocal primary timeout、local retry失敗後、規定どおりCodex `gpt-5.6-sol` fallbackでvalidatedとなった。両runはsucceededで、`unsafe_model_override`は0件だった。

API操作は隔離環境のsession、status、question read、preview、start、job read、question-run readだけに限定した。Firestore、publication、production patchへの呼出しは0で、実repoと隔離copyの対象`00_source` SHA-256は一致した。thread、session、turn、job、内部question IDと一時directory suffixは成果物から除外した。
