import { Link } from "../components/ui/controls";
const shouldNavigateInApp = (event, target) =>
	!event.defaultPrevented &&
	event.button === 0 &&
	!event.metaKey &&
	!event.ctrlKey &&
	!event.shiftKey &&
	!event.altKey &&
	(!target || target === "_self");

export default function RouteLink({ to, navigate, replace = false, target, onClick, children, ...props }) {
	const handleClick = (event) => {
		onClick?.(event);
		if (!navigate || !shouldNavigateInApp(event, target)) {
			return;
		}
		event.preventDefault();
		navigate(to, { replace });
	};

	return (
		<Link href={to} target={target} onClick={handleClick} {...props}>
			{children}
		</Link>
	);
}
