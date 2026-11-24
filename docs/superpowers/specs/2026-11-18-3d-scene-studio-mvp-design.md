# 3D Sahne Stüdyosu MVP — Tasarım

Tarih: 2026-11-18
Durum: Onaylandı (kullanıcı: "tamam yap", Adım 1)

## Amaç

Her objenin sınıf/kimlik ve iskelet (kemik) verisiyle tutulabildiği bir sahne veri modeli ve glTF varlıkların her yönden görüntülenebildiği bir 3D görüntüleyici (MVP) sağlamak. Sonraki adımlar (3D üretim, rigging/animasyon, fizik, gerçek zamanlı düzenleme) bu şemanın üzerine kurulacak.

## Kapsam (MVP)

- **Modeller (agent_app):**
  - `Scene`: id, user, name, description, graph/meta (JSON), timestamps.
  - `SceneAsset`: id, user, scene (opsiyonel), name, file (glTF/glb), file_type, metadata (JSON), timestamps.
  - `SceneNode`: id, scene, parent (opsiyonel), name, node_type, components (JSON: identity, transform, mesh, skeleton/bones, animation/physics placeholder), order, timestamps.
- **Bileşen şeması (JSON, genişletilebilir):**
  - `identity`: stable_id, label, locked_fields (yüz/oran/aksesuar vb. değişmeyecek alanlar).
  - `transform`: position [x,y,z], rotation [x,y,z,euler], scale [x,y,z].
  - `mesh`: asset_id/ref, primitive/material refs.
  - `skeleton`: root, bones[{name,parent,head,tail,rest_matrix/transform}], joints/constraints (opsiyonel).
  - `animation`, `physics`: boş/placeholder.
- **3D Görüntüleyici:** Three.js (CDN) ile glTF/glb yükleme, OrbitControls (her yönden bakış), ışık, grid/zemin, sahne/node listesi.
- **UI:** `/studio/` sayfası — sahne listesi, sahne oluştur, glTF yükle, görüntüleyicide aç.
- **API:** sahne CRUD, asset yükleme, node CRUD/listeleme; kullanıcı izolasyonu ve auth.
- **Depolama:** `media/scenes/<user>/assets/`.

## Güvenlik ve kısıtlar

- Tüm uçlar kimlik doğrulaması ister; kullanıcı yalnızca kendi sahnelerini görür.
- Dosya yükleme: glTF/glb (ve ilişkili) ile sınırlı, boyut sınırı (config).
- Yol traversal reddi.

## Non-goals (MVP)

- 3D model üretimi (text-to-3D), otomatik rigging, fizik simülasyonu, gerçek zamanlı çoklu kullanıcı düzenleme.
- Tam timeline/keyframe editörü (sonraki adımlar).