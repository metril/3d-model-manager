/**
 * R3F scene for the plate editor (lazy-loaded by `PlateEditorDialog`). Z-up in
 * millimetres, bed corner at the origin: GLB derivatives are already mm and
 * keep the source mesh's axes, so no conversion is needed. Each part is
 * wrapped so its footprint centre / bottom sits at the group origin, matching
 * the server pose contract T(x,y,0)·Rz·S·T(-cx,-cy,-zmin).
 */
import { Suspense, useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { Canvas, type ThreeEvent } from "@react-three/fiber";
import { OrbitControls, TransformControls, useGLTF } from "@react-three/drei";
import * as THREE from "three";

import type { PlateItem } from "@/components/plate/poseStore";

export type PlateMode = "translate" | "rotate" | "scale";

export interface ScenePart {
  file_id: number;
  url: string;
  color: string;
}

interface Bed {
  x_mm: number;
  y_mm: number;
  height: number | null;
}

function PartMesh({ url, tint }: { url: string; tint: string | null }) {
  const gltf = useGLTF(url);
  const { object, offset } = useMemo(() => {
    const clone = gltf.scene.clone(true);
    clone.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (mesh.isMesh) mesh.material = (mesh.material as THREE.Material).clone();
    });
    const box = new THREE.Box3().setFromObject(clone);
    const offset = new THREE.Vector3(-(box.min.x + box.max.x) / 2, -(box.min.y + box.max.y) / 2, -box.min.z);
    return { object: clone, offset };
  }, [gltf]);

  useLayoutEffect(() => {
    object.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      const mat = mesh.material as THREE.MeshStandardMaterial;
      if (mat.color) mat.emissive?.set(tint ?? "#000000");
      if (mat.emissive) mat.emissiveIntensity = tint ? 0.55 : 0;
    });
  }, [object, tint]);

  return <primitive object={object} position={offset} />;
}

function PlatePart({
  item,
  part,
  selected,
  fits,
  mode,
  freeRotate,
  onSelect,
  onBegin,
  onChange,
  onOrbitLock,
}: {
  item: PlateItem;
  part: ScenePart;
  selected: boolean;
  fits: boolean;
  mode: PlateMode;
  freeRotate: boolean;
  onSelect: (uid: string) => void;
  onBegin: () => void;
  onChange: (uid: string, fields: { x_mm: number; y_mm: number; rot_z_deg: number; scale: number }) => void;
  onOrbitLock: (locked: boolean) => void;
}) {
  const ref = useRef<THREE.Group>(null);
  const tint = fits ? (selected ? part.color : null) : "#ef4444";

  return (
    <>
      <group
        ref={ref}
        position={[item.x_mm, item.y_mm, 0]}
        rotation={[0, 0, (item.rot_z_deg * Math.PI) / 180]}
        scale={item.scale}
        onClick={(e: ThreeEvent<MouseEvent>) => {
          e.stopPropagation();
          onSelect(item.uid);
        }}
      >
        <Suspense fallback={null}>
          <PartMesh url={part.url} tint={tint} />
        </Suspense>
      </group>
      {selected && ref.current ? (
        <TransformControls
          object={ref.current}
          mode={mode}
          size={0.8}
          showX={mode !== "rotate"}
          showY={mode !== "rotate"}
          showZ={mode !== "translate"}
          space={mode === "translate" ? "world" : "local"}
          translationSnap={null}
          rotationSnap={mode === "rotate" && !freeRotate ? Math.PI / 12 : null}
          scaleSnap={null}
          onMouseDown={() => {
            onOrbitLock(true);
            onBegin();
          }}
          onMouseUp={() => onOrbitLock(false)}
          onObjectChange={() => {
            const g = ref.current;
            if (!g) return;
            // Z locked to the bed; rotation about Z only; scale uniform.
            g.position.z = 0;
            g.rotation.x = 0;
            g.rotation.y = 0;
            const s = mode === "scale" ? g.scale.x : item.scale;
            g.scale.setScalar(s);
            onChange(item.uid, {
              x_mm: g.position.x,
              y_mm: g.position.y,
              rot_z_deg: THREE.MathUtils.radToDeg(g.rotation.z),
              scale: s,
            });
          }}
        />
      ) : null}
    </>
  );
}

function BedVolume({ bed }: { bed: Bed }) {
  const h = bed.height ?? 0;
  return (
    <group>
      <mesh position={[bed.x_mm / 2, bed.y_mm / 2, -0.05]} onClick={(e) => e.stopPropagation()}>
        <planeGeometry args={[bed.x_mm, bed.y_mm]} />
        <meshStandardMaterial color="#1e293b" transparent opacity={0.85} side={THREE.DoubleSide} />
      </mesh>
      <gridHelper
        args={[Math.max(bed.x_mm, bed.y_mm), Math.round(Math.max(bed.x_mm, bed.y_mm) / 10), "#475569", "#334155"]}
        position={[bed.x_mm / 2, bed.y_mm / 2, 0]}
        rotation={[Math.PI / 2, 0, 0]}
      />
      {h > 0 ? (
        <mesh position={[bed.x_mm / 2, bed.y_mm / 2, h / 2]}>
          <boxGeometry args={[bed.x_mm, bed.y_mm, h]} />
          <meshBasicMaterial color="#2dd4bf" transparent opacity={0.05} depthWrite={false} />
        </mesh>
      ) : null}
      <lineSegments position={[bed.x_mm / 2, bed.y_mm / 2, (h || 1) / 2]}>
        <edgesGeometry args={[new THREE.BoxGeometry(bed.x_mm, bed.y_mm, h || 1)]} />
        <lineBasicMaterial color="#2dd4bf" transparent opacity={0.6} />
      </lineSegments>
    </group>
  );
}

function OrbitTarget({ bed, locked }: { bed: Bed; locked: boolean }) {
  return <OrbitControls makeDefault enabled={!locked} target={[bed.x_mm / 2, bed.y_mm / 2, 0]} />;
}

export default function PlateScene({
  bed,
  items,
  parts,
  selected,
  fitsByUid,
  mode,
  freeRotate,
  orbitLocked,
  onSelect,
  onBegin,
  onChange,
  onOrbitLock,
}: {
  bed: Bed;
  items: PlateItem[];
  parts: Map<number, ScenePart>;
  selected: string | null;
  fitsByUid: Map<string, boolean>;
  mode: PlateMode;
  freeRotate: boolean;
  orbitLocked: boolean;
  onSelect: (uid: string | null) => void;
  onBegin: () => void;
  onChange: (uid: string, fields: { x_mm: number; y_mm: number; rot_z_deg: number; scale: number }) => void;
  onOrbitLock: (locked: boolean) => void;
}) {
  const reach = Math.max(bed.x_mm, bed.y_mm);
  useEffect(() => {
    THREE.Object3D.DEFAULT_UP.set(0, 0, 1);
  }, []);
  return (
    <Canvas
      camera={{ position: [bed.x_mm / 2, -reach * 0.9, reach * 0.9], up: [0, 0, 1], fov: 40, near: 1, far: reach * 20 }}
      onPointerMissed={() => onSelect(null)}
    >
      <ambientLight intensity={1.1} />
      <directionalLight position={[reach, -reach, reach * 2]} intensity={2.2} />
      <directionalLight position={[-reach, reach, reach]} intensity={0.8} />
      <BedVolume bed={bed} />
      <OrbitTarget bed={bed} locked={orbitLocked} />
      {items.map((item) => {
        const part = parts.get(item.file_id);
        return part ? (
          <PlatePart
            key={item.uid}
            item={item}
            part={part}
            selected={item.uid === selected}
            fits={fitsByUid.get(item.uid) ?? true}
            mode={mode}
            freeRotate={freeRotate}
            onSelect={onSelect}
            onBegin={onBegin}
            onChange={onChange}
            onOrbitLock={onOrbitLock}
          />
        ) : null;
      })}
    </Canvas>
  );
}
