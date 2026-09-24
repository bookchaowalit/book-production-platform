using UnityEngine;

namespace BookProduction.Runtime
{
    public interface IGameInputSource
    {
        Vector2 ReadMove();
    }

    internal sealed class KeyboardGameInputSource : IGameInputSource
    {
        public Vector2 ReadMove()
        {
            float horizontal = 0f;
            float vertical = 0f;
            if (Input.GetKey(KeyCode.A) || Input.GetKey(KeyCode.LeftArrow)) horizontal -= 1f;
            if (Input.GetKey(KeyCode.D) || Input.GetKey(KeyCode.RightArrow)) horizontal += 1f;
            if (Input.GetKey(KeyCode.S) || Input.GetKey(KeyCode.DownArrow)) vertical -= 1f;
            if (Input.GetKey(KeyCode.W) || Input.GetKey(KeyCode.UpArrow)) vertical += 1f;
            return new Vector2(horizontal, vertical);
        }
    }

    [RequireComponent(typeof(Rigidbody2D))]
    [RequireComponent(typeof(BoxCollider2D))]
    public sealed class TopDownPlayerController : MonoBehaviour
    {
        [SerializeField] private float moveSpeed = 5f;
        private Rigidbody2D body;
        private GameProgress progress;

        public IGameInputSource InputSource { get; set; } = new KeyboardGameInputSource();

        public void Initialize(float speed, GameProgress gameProgress)
        {
            moveSpeed = speed;
            progress = gameProgress;
        }

        private void Awake()
        {
            body = GetComponent<Rigidbody2D>();
            if (progress == null)
            {
                progress = FindFirstObjectByType<GameProgress>();
            }
            if (InputSource == null)
            {
                InputSource = new KeyboardGameInputSource();
            }
        }

        private void FixedUpdate()
        {
            if (body == null || InputSource == null)
            {
                return;
            }

            Vector2 direction = Vector2.ClampMagnitude(InputSource.ReadMove(), 1f);
            if (direction.sqrMagnitude > 0f)
            {
                if (progress != null)
                {
                    progress.RecordPlayerMoved();
                }
                body.MovePosition(body.position + direction * moveSpeed * Time.fixedDeltaTime);
            }
        }
    }
}
