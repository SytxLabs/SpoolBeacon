import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { ThreeMFLoader } from "three/addons/loaders/3MFLoader.js";

function loadGeometryOrGroup(ext, buffer) {
    if (ext === "stl") {
        return new STLLoader().parse(buffer);
    }
    if (ext === "3mf") {
        return new ThreeMFLoader().parse(buffer);
    }
    throw new Error(`Unsupported file extension: ${ext}`);
}

function frameObject(camera, controls, object) {
    const box = new THREE.Box3().setFromObject(object);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z) || 1;

    object.position.sub(center);

    const distance = maxDim * 1.8;
    camera.near = maxDim / 100;
    camera.far = maxDim * 100;
    camera.position.set(distance, distance, distance);
    camera.updateProjectionMatrix();

    controls.target.set(0, 0, 0);
    controls.update();
}

export function initViewer(canvas, fileUrl, ext) {
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0a0f);

    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const keyLight = new THREE.DirectionalLight(0xffffff, 1.2);
    keyLight.position.set(1, 1.5, 1);
    scene.add(keyLight);
    const fillLight = new THREE.DirectionalLight(0xffffff, 0.4);
    fillLight.position.set(-1, -0.5, -1);
    scene.add(fillLight);

    function resize() {
        const width = canvas.clientWidth || 1;
        const height = canvas.clientHeight || 1;
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
    }

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(canvas);
    resize();

    let animationFrame = null;
    function animate() {
        animationFrame = requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
    }
    animate();

    let loadedObject = null;
    fetch(fileUrl)
        .then((response) => response.arrayBuffer())
        .then((buffer) => {
            const result = loadGeometryOrGroup(ext, buffer);
            if (result.isBufferGeometry) {
                const material = new THREE.MeshStandardMaterial({ color: 0x818cf8, metalness: 0.1, roughness: 0.6 });
                loadedObject = new THREE.Mesh(result, material);
            } else {
                loadedObject = result;
            }
            // STL/3MF files use the Z-up convention of slicers/printers;
            // three.js scenes are Y-up, so without this the model lies on its side.
            loadedObject.rotation.x = -Math.PI / 2;
            scene.add(loadedObject);
            frameObject(camera, controls, loadedObject);
        })
        .catch((error) => {
            console.error("Failed to load 3D preview:", error);
        });

    return {
        dispose() {
            cancelAnimationFrame(animationFrame);
            resizeObserver.disconnect();
            controls.dispose();
            renderer.dispose();
            scene.traverse((child) => {
                if (child.geometry) child.geometry.dispose();
                if (child.material) {
                    (Array.isArray(child.material) ? child.material : [child.material]).forEach((m) => m.dispose());
                }
            });
        },
    };
}
