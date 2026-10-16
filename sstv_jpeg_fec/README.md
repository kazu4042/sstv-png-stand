# SSTV Turbo JPEG FEC (固定長パケット・消失訂正・即時タイル復元システム)

本システムは、無線通信（4-FSK / SSTV Turbo）における世界規模の分散受信を想定し、
**「少しでも受信できたら即座に一部分がタイル描画されるリアルタイムUX」** と
**「世界各地の受信データを集約し、冗長パケットから100%完全復元する宇宙通信理論」**
を融合させた新世代の画像伝送プロトコルです。

---

## 1. 2つの顔を持つ研究アーキテクチャ

* **【表テーマ（市民参加型・教育）】**:
  パケットを受信するたびに、該当する16×16ピクセルのタイルが即座に画面に表示される。
* **【裏テーマ（宇宙通信・工学）】**:
  世界中の地上局が電波障害（パケット消失20%以上）に遭いながら分散受信したデータをクラウドで集約し、
  4+1 XORパリティおよび噴水パリティ（Peeling Decoder）を用いて欠損パケットを代数逆算し、**100%完全復元（BER=0, PSNR 100dB）** を達成する。

---

## 2. ディレクトリ構成

```
sstv-jpeg-fec/
├── config.py                 # 全体設定 (後からタイルサイズやFEC比率を動的変更可能)
├── core/
│   ├── manifest.py           # タイル境界マニフェスト (目録管理)
│   ├── fec_engine.py         # 固定長スライス & 4+1パリティ & 噴水パリティ & 剥ぎ取り復号
│   ├── encoder.py            # JPEG圧縮 ➔ マスターバイナリ化 ➔ FECパケット生成 ➔ 4-FSK音声化
│   └── aggregator.py         # パケット集約 ➔ 消失逆算 ➔ 即時タイルデコード
├── run_simulation.py         # 単体および複数地上局の分散受信シミュレーションCLI
├── data/
│   ├── input/                # 送信用画像 (test.jpg)
│   └── output/               # 復元画像 (multi_station_recovered.png など)
└── README.md
```

---

## 3. 設定変更の自由度 (config.py)

[config.py](file:///c:/Users/kosei/OneDrive/Desktop/sstv-jpeg-fec/config.py) 内のパラメータは、コード修正なしで柔軟に変更可能です：

* `payload_size`: 固定長パケットサイズ (デフォルト: 256バイト)
* `fec_group_size`: パリティ間隔 (デフォルト: 4 ➔ 4+1方式)
* `enable_fountain_packets`: 噴水パリティの付与 (デフォルト: True)
* `tile_size`: タイルサイズ (デフォルト: 16×16)
* `jpeg_quality`: JPEG品質 (デフォルト: 75)

---

## 4. 実行方法

### ① 複数地上局（例: 3局、各局20%パケット消失）による完全復元シミュレーション
```powershell
python run_simulation.py --mode multi --stations 3 --drop_rate 0.20
```

### ② 単一地上局（例: 8%パケット消失）のシミュレーション
```powershell
python run_simulation.py --mode single --drop_rate 0.08
```
